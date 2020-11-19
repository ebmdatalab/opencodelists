from collections import defaultdict

import structlog
from django.db import transaction
from django.db.models import Count
from django.utils.text import slugify

from codelists.hierarchy import Hierarchy

from .models import CodeObj, SearchResult

logger = structlog.get_logger()


def create_draft(*, owner, name, coding_system_id):
    draft = owner.drafts.create(
        name=name, slug=slugify(name), coding_system_id=coding_system_id
    )

    logger.info("Create Codelist", draft_pk=draft.pk)

    return draft


@transaction.atomic
def create_draft_with_codes(*, owner, name, coding_system_id, codes):
    draft = owner.drafts.create(
        name=name, slug=slugify(name), coding_system_id=coding_system_id
    )

    CodeObj.objects.bulk_create(CodeObj(draft=draft, code=code) for code in codes)

    logger.info("Create Codelist with codes", draft_pk=draft.pk)

    return draft


@transaction.atomic
def create_search(*, draft, term, codes):
    search = draft.searches.create(term=term, slug=slugify(term))

    # Ensure that there is a CodeObj object linked to this draft for each code.
    codes_with_existing_code_objs = set(
        draft.code_objs.filter(code__in=codes).values_list("code", flat=True)
    )
    codes_without_existing_code_objs = set(codes) - codes_with_existing_code_objs
    CodeObj.objects.bulk_create(
        CodeObj(draft=draft, code=code) for code in codes_without_existing_code_objs
    )

    # Create a SearchResult for each code.
    code_obj_ids = draft.code_objs.filter(code__in=codes).values_list("id", flat=True)
    SearchResult.objects.bulk_create(
        SearchResult(search=search, code_obj_id=id) for id in code_obj_ids
    )

    logger.info("Created Search", search_pk=search.pk)

    return search


@transaction.atomic
def delete_search(*, search):
    # Grab the PK before we delete the instance
    search_pk = search.pk

    # Delete any CodeObjs that only belong to this search
    search.draft.code_objs.annotate(num_results=Count("results")).filter(
        results__search=search, num_results=1
    ).delete()

    # Delete the search
    search.delete()

    logger.info("Deleted Search", search_pk=search_pk)


@transaction.atomic
def update_code_statuses(*, draft, updates):
    code_to_status = dict(draft.code_objs.values_list("code", "status"))
    h = Hierarchy.from_codes(draft.coding_system, list(code_to_status))
    new_code_to_status = h.update_node_to_status(code_to_status, updates)

    status_to_new_code = defaultdict(list)
    for code, status in new_code_to_status.items():
        status_to_new_code[status].append(code)

    for status, codes in status_to_new_code.items():
        draft.code_objs.filter(code__in=codes).update(status=status)

    logger.info("Updated Codelist Statuses", draft_pk=draft.pk)
