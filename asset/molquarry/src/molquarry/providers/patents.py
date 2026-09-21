"""Patent services with explicit authentication and bounded queries."""

import base64
import json
import re
import xml.etree.ElementTree as ET
from typing import Literal
from uuid import uuid4

from pydantic import Field

from ..errors import MolQuarryError
from ..models import InputModel
from .base import Operation, Page, Provider
from .common import Search, Text, credential, records


class EPOSearch(Search):
    limit: int = Field(default=20, ge=1, le=100)


class EPOPublication(InputModel):
    publication: str = Field(pattern=r"^[A-Z]{2}[A-Za-z0-9.]{3,30}$")
    format: Literal["epodoc", "docdb"] = "epodoc"


class EPO(Provider):
    id = "epo"
    env = ("EPO_CONSUMER_KEY", "EPO_CONSUMER_SECRET")
    operations = {
        "search": Operation(
            "OPS CQL search; returns XML publication references with namespaces retained",
            EPOSearch,
            {"query": "ti=kinase", "limit": 5},
            env,
        ),
        "family": Operation(
            "INPADOC family + bibliography XML", EPOPublication, {"publication": "EP1000000"}, env
        ),
        "legal": Operation(
            "OPS legal events XML for one publication",
            EPOPublication,
            {"publication": "EP1000000"},
            env,
        ),
    }

    def token(self):
        key, secret = (credential(k, self.id) for k in self.env)
        basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
        r = self.request(
            "POST",
            "https://ops.epo.org/3.2/auth/accesstoken",
            form={"grant_type": "client_credentials"},
            headers={"Authorization": "Basic " + basic},
            private=True,
        )
        return r.data["access_token"]

    def query(self, operation, params):
        if operation == "search" and params.offset + params.limit > 2000:
            raise MolQuarryError(
                "pagination_limit", "OPS search range is limited to 2000; narrow the CQL query"
            )
        token = self.token()
        if operation == "search":
            route = "published-data/search"
            query = {
                "q": params.query,
                "Range": f"{params.offset + 1}-{params.offset + params.limit}",
            }
        else:
            route = f"{operation}/publication/{params.format}/{params.publication}"
            if operation == "family":
                route += "/biblio"
            query = {}
        r = self.http.text(
            self.spec,
            "GET",
            f"https://ops.epo.org/3.2/rest-services/{route}",
            params=query,
            headers={"Authorization": "Bearer " + token, "Accept": "application/xml"},
            private=True,
        )
        if "<!DOCTYPE" in r.data.upper() or "<!ENTITY" in r.data.upper():
            raise MolQuarryError("invalid_response", "XML document types/entities are not accepted")
        try:
            root = ET.fromstring(r.data)
        except ET.ParseError as exc:
            raise MolQuarryError("invalid_response", "OPS response is not XML") from exc
        if operation != "search":
            return Page([{"xml": r.data}], [r.provenance])
        found = root.find(".//{*}biblio-search")
        if found is None:
            raise ValueError("Missing OPS biblio-search")
        total = int(found.attrib["total-result-count"])
        items = found.findall(".//{*}publication-reference")
        rows = [{"xml": ET.tostring(item, encoding="unicode")} for item in items]
        more = params.offset + len(rows) < min(total, 2000)
        return Page(
            rows,
            [r.provenance],
            total,
            {**params.model_dump(), "offset": params.offset + params.limit}
            if more and rows
            else None,
            [
                (
                    "OPS search is limited to its accessible result window; legal-event data "
                    "requires jurisdiction-specific interpretation."
                )
            ],
        )


class USPTOApplication(InputModel):
    application_number: str = Field(pattern=r"^\d{8,15}$")


class USPTO(Provider):
    id = "uspto"
    operations = {
        "application": Operation(
            (
                "ODP Patent File Wrapper application record (requires account; live schema "
                "validation pending credentials)"
            ),
            USPTOApplication,
            {"application_number": "16123456"},
            ("USPTO_API_KEY",),
        )
    }

    def query(self, operation, params):
        r = self.request(
            "GET",
            f"https://api.uspto.gov/api/v1/patent/applications/{params.application_number}",
            headers={"X-API-KEY": credential("USPTO_API_KEY", self.id)},
            private=True,
        )
        return Page(
            records(r.data),
            [r.provenance],
            warnings=[
                (
                    "ODP application envelope retained as returned; adapter has not been "
                    "live-tested with a licensed account."
                )
            ],
        )


class PatentQuery(InputModel):
    keyword: Text
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    limit: int = Field(default=10, ge=1, le=100)
    maximum_bytes_billed: int = Field(default=1073741824, ge=1)
    dry_run: bool = True


class BigQueryJob(InputModel):
    job_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,200}$")
    location: str = Field(default="US", pattern=r"^[A-Za-z0-9_-]{1,50}$")
    page_token: str | None = Field(default=None, max_length=3000)
    limit: int = Field(default=20, ge=1, le=100)


class GooglePatents(Provider):
    id = "google_patents"
    env = ("GOOGLE_CLOUD_PROJECT", "GOOGLE_ACCESS_TOKEN")
    operations = {
        "query_plan": Operation(
            "Build a parameterized BigQuery SELECT; offline, no billable request",
            PatentQuery,
            {"keyword": "EGFR"},
        ),
        "search": Operation(
            "Execute the fixed patent title query; dry_run=true by default, explicit scan budget",
            PatentQuery,
            {"keyword": "EGFR"},
            env,
        ),
        "job_results": Operation(
            "Read or paginate a previously submitted BigQuery job",
            BigQueryJob,
            {"job_id": "job_example"},
            env,
        ),
    }
    sql = """SELECT publication_number, family_id, country_code, publication_date,
        (SELECT text FROM UNNEST(title_localized) WHERE language='en' LIMIT 1) AS title
        FROM `patents-public-data.patents.publications`
        WHERE country_code=@country AND EXISTS (
            SELECT 1 FROM UNNEST(title_localized) t WHERE t.language='en'
            AND STRPOS(LOWER(t.text), LOWER(@keyword)) > 0)
        ORDER BY publication_date DESC, publication_number LIMIT @limit"""

    def query(self, operation, params):
        if operation != "job_results":
            body = {
                "query": self.sql,
                "useLegacySql": False,
                "parameterMode": "NAMED",
                "queryParameters": [
                    {
                        "name": name,
                        "parameterType": {"type": kind},
                        "parameterValue": {"value": str(value)},
                    }
                    for name, kind, value in [
                        ("country", "STRING", params.country),
                        ("keyword", "STRING", params.keyword),
                        ("limit", "INT64", params.limit),
                    ]
                ],
                "maximumBytesBilled": str(params.maximum_bytes_billed),
                "dryRun": params.dry_run,
                "timeoutMs": 10000,
                "maxResults": params.limit,
                "location": "US",
            }
            if operation == "query_plan":
                return Page(
                    [body],
                    warnings=[
                        (
                            "SQL plan only. LIMIT does not limit scanned bytes; dry-run before "
                            "raising the billing budget."
                        )
                    ],
                )
        project = credential("GOOGLE_CLOUD_PROJECT", self.id)
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
            raise MolQuarryError("invalid_credentials", "Invalid Google Cloud project ID")
        headers = {"Authorization": "Bearer " + credential("GOOGLE_ACCESS_TOKEN", self.id)}
        base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
        if operation == "search":
            # Internal HTTP retries reuse this ID, so a retried billed query is not resubmitted.
            body["requestId"] = str(uuid4())
            r = self.request("POST", base, body=body, headers=headers, private=True)
        else:
            r = self.request(
                "GET",
                f"{base}/{params.job_id}",
                params={
                    "location": params.location,
                    "pageToken": params.page_token,
                    "maxResults": params.limit,
                },
                headers=headers,
                private=True,
            )
        data = r.data
        if operation == "search" and params.dry_run:
            return Page(
                [{**data, "molquarry_result_kind": "dry_run"}],
                [r.provenance],
                warnings=["Cost estimate only; no patent result rows were retrieved."],
            )
        if not data.get("jobComplete", False):
            return Page(
                [{**data, "molquarry_result_kind": "pending_job"}],
                [r.provenance],
                warnings=[
                    (
                        "Job still running. Call job_results with returned jobReference.jobId "
                        "and location."
                    )
                ],
            )
        fields = [f["name"] for f in data["schema"]["fields"]]
        rows = [
            dict(zip(fields, [f["v"] for f in row["f"]], strict=True))
            for row in data.get("rows", [])
        ]
        next_ = None
        warnings = []
        if data.get("pageToken"):
            ref = data["jobReference"]
            continuation = {
                "job_id": ref["jobId"],
                "location": ref.get("location", "US"),
                "page_token": data["pageToken"],
                "limit": params.limit,
            }
            if operation == "job_results":
                next_ = continuation
            else:
                warnings.append("Continue with job_results: " + json.dumps(continuation))
        return Page(rows, [r.provenance], int(data.get("totalRows", len(rows))), next_, warnings)
