import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from codelists.hierarchy import Hierarchy
from codelists.search import do_search

from . import actions
from .decorators import load_draft

NO_SEARCH_TERM = object()


@login_required
@load_draft
def draft(request, draft):
    if request.POST:
        return _handle_post(request, draft)
    return _draft(request, draft, None)


def _handle_post(request, draft):
    action = request.POST["action"]
    if action == "save":
        actions.save(draft=draft)
        messages.add_message(request, messages.INFO, "A new version has been saved")
        return redirect(draft)
    elif action == "save-draft":
        messages.add_message(
            request, messages.INFO, "Your changes have been saved as draft"
        )
        return redirect(draft.codelist)
    elif action == "discard":
        messages.add_message(request, messages.INFO, "Your changes have been discarded")
        actions.discard_draft(draft=draft)
        return redirect(draft.codelist)


@login_required
@load_draft
def search(request, draft, search_slug):
    return _draft(request, draft, search_slug)


@login_required
@load_draft
def no_search_term(request, draft):
    return _draft(request, draft, NO_SEARCH_TERM)


def _draft(request, draft, search_slug):
    coding_system = draft.coding_system

    code_to_status = dict(draft.code_objs.values_list("code", "status"))
    all_codes = list(code_to_status)

    included_codes = [c for c in all_codes if code_to_status[c] == "+"]
    excluded_codes = [c for c in all_codes if code_to_status[c] == "-"]

    if search_slug is None:
        search = None
        displayed_codes = list(code_to_status)
    elif search_slug is NO_SEARCH_TERM:
        search = NO_SEARCH_TERM
        displayed_codes = list(
            draft.code_objs.filter(results=None).values_list("code", flat=True)
        )
    else:
        search = get_object_or_404(draft.searches, slug=search_slug)
        displayed_codes = list(search.results.values_list("code_obj__code", flat=True))

    searches = [
        {
            "term": s.term,
            "url": draft.get_builder_url("search", s.slug),
            "active": s == search,
        }
        for s in draft.searches.order_by("term")
    ]

    if searches and draft.code_objs.filter(results=None).exists():
        searches.append(
            {
                "term": "[no search term]",
                "url": draft.get_builder_url("no-search-term"),
                "active": search_slug == NO_SEARCH_TERM,
            }
        )

    filter = request.GET.get("filter")
    if filter == "included":
        displayed_codes = [c for c in displayed_codes if "+" in code_to_status[c]]
    elif filter == "excluded":
        displayed_codes = [c for c in displayed_codes if "-" in code_to_status[c]]
    elif filter == "unresolved":
        displayed_codes = [c for c in displayed_codes if code_to_status[c] == "?"]
    elif filter == "in-conflict":
        displayed_codes = [c for c in displayed_codes if code_to_status[c] == "!"]
        filter = "in conflict"

    hierarchy = Hierarchy.from_codes(coding_system, all_codes)

    ancestor_codes = hierarchy.filter_to_ultimate_ancestors(set(displayed_codes))
    code_to_term = coding_system.code_to_term(hierarchy.nodes | set(all_codes))
    tree_tables = sorted(
        (type, sorted(codes, key=code_to_term.__getitem__))
        for type, codes in coding_system.codes_by_type(
            ancestor_codes, hierarchy
        ).items()
    )

    update_url = draft.get_builder_url("update")
    search_url = draft.get_builder_url("new-search")

    ctx = {
        "user": draft.draft_owner,
        "draft": draft,
        "draft_url": draft.get_builder_url("draft"),
        "codelist_name": draft.codelist.name,
        "search": search,
        "NO_SEARCH_TERM": NO_SEARCH_TERM,
        # The following values are passed to the CodelistBuilder component.
        # When any of these chage, use generate_builder_fixture to update
        # static/test/js/fixtures/elbow.json.
        # {
        "searches": searches,
        "filter": filter,
        "tree_tables": tree_tables,
        "all_codes": all_codes,
        "included_codes": included_codes,
        "excluded_codes": excluded_codes,
        "parent_map": {p: list(cc) for p, cc in hierarchy.parent_map.items()},
        "child_map": {c: list(pp) for c, pp in hierarchy.child_map.items()},
        "code_to_term": code_to_term,
        "code_to_status": code_to_status,
        "is_editable": request.user == draft.draft_owner,
        "update_url": update_url,
        "search_url": search_url,
        # }
    }

    return render(request, "builder/draft.html", ctx)


@login_required
@require_http_methods(["POST"])
@load_draft
def update(request, draft):
    updates = json.loads(request.body)["updates"]
    actions.update_code_statuses(draft=draft, updates=updates)
    return JsonResponse({"updates": updates})


@login_required
@require_http_methods(["POST"])
@load_draft
def new_search(request, draft):
    term = request.POST["term"]
    codes = do_search(draft.coding_system, term)["all_codes"]
    if not codes:
        # TODO message about no hits
        return redirect(draft)

    search = actions.create_search(draft=draft, term=term, codes=codes)
    return redirect(draft.get_builder_url("search", search.slug))
