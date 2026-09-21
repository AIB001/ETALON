from typing import Literal

from pydantic import Field

from ..models import InputModel
from .base import Operation, Page, Provider

BASE = "https://clinicaltrials.gov/api/v2/studies"
Status = Literal[
    "RECRUITING",
    "NOT_YET_RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "COMPLETED",
    "TERMINATED",
    "WITHDRAWN",
    "SUSPENDED",
    "ENROLLING_BY_INVITATION",
    "UNKNOWN",
    "AVAILABLE",
    "NO_LONGER_AVAILABLE",
    "TEMPORARILY_NOT_AVAILABLE",
    "APPROVED_FOR_MARKETING",
    "WITHHELD",
]


class StudyInput(InputModel):
    nct_id: str = Field(pattern=r"^NCT[0-9]{8}$")


class StudySearch(InputModel):
    query: str = Field(min_length=1, max_length=2000)
    condition: str | None = Field(default=None, max_length=500)
    intervention: str | None = Field(default=None, max_length=500)
    status: Status | None = None
    limit: int = Field(default=10, ge=1, le=100)
    page_token: str | None = Field(default=None, max_length=20000)


class ClinicalTrials(Provider):
    id = "clinicaltrials"
    operations = {
        "study": Operation(
            "Full public study record by NCT ID.", StudyInput, {"nct_id": "NCT03257124"}
        ),
        "search": Operation(
            "Search trial summaries by terms, condition, intervention and status.",
            StudySearch,
            {"query": "EGFR inhibitor", "limit": 5},
        ),
    }

    def query(self, operation, params):
        if operation == "study":
            response = self.request("GET", f"{BASE}/{params.nct_id}")
            return Page([response.data], [response.provenance])
        response = self.request(
            "GET",
            BASE,
            params={
                "query.term": params.query,
                "query.cond": params.condition,
                "query.intr": params.intervention,
                "filter.overallStatus": params.status,
                "pageSize": params.limit,
                "pageToken": params.page_token,
                "format": "json",
                "countTotal": "true",
                "fields": "NCTId,BriefTitle,OverallStatus,Phase,Condition,InterventionName,"
                "LeadSponsorName,LastUpdatePostDate",
            },
        )
        next_params = None
        # totalCount is normally present only on the first page, even with countTotal=true.
        if "studies" not in response.data and response.data.get("totalCount") != 0:
            from ..errors import MolQuarryError

            raise MolQuarryError(
                "invalid_response", "Missing clinical study results", source=self.id
            )
        if response.data.get("nextPageToken"):
            next_params = {
                **params.model_dump(exclude_none=True),
                "page_token": response.data["nextPageToken"],
            }
        return Page(
            response.data.get("studies", []),
            [response.provenance],
            response.data.get("totalCount"),
            next_params,
            [
                "Trial status and phase do not establish regulatory approval or efficacy.",
                "Search expands terms and aliases; an intervention may use a brand name.",
            ],
        )
