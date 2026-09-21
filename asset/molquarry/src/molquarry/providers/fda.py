"""openFDA drug records and partitioned release downloads."""

from pydantic import Field

from ..errors import MolQuarryError
from ..models import InputModel
from .base import Operation, Provider
from .common import Search, offset_page


class Partition(InputModel):
    partition: int = Field(default=0, ge=0)


class OpenFDA(Provider):
    operations = {
        "search": Operation(
            "Search using official openFDA query syntax",
            Search,
            {"query": 'products.active_ingredients.name:"ASPIRIN"', "limit": 5},
        )
    }
    downloads = {
        "partition": Operation(
            "Resolve a JSON ZIP partition from the current official download manifest",
            Partition,
            {},
        )
    }
    download_hosts = frozenset({"download.open.fda.gov"})

    def query(self, operation, params):
        if params.offset > 25000:
            raise MolQuarryError(
                "pagination_limit",
                "openFDA skip is limited to 25000; use bulk downloads",
                source=self.id,
            )
        # openFDA returns 404 for a valid search with no matches. Keep it as a
        # structured not_found error instead of swallowing other 404 failures.
        r = self.request(
            "GET",
            f"https://api.fda.gov/drug/{self.endpoint}.json",
            params={"search": params.query, "limit": params.limit, "skip": params.offset},
        )
        r.provenance.source_version = r.data["meta"].get("last_updated")
        page = offset_page(r.data["results"], r, params, r.data["meta"]["results"]["total"])
        if page.next_parameters and page.next_parameters["offset"] > 25000:
            page.next_parameters = None
            page.warnings.append(
                "openFDA skip cap reached; remaining records require bulk or a narrower search."
            )
        return page

    def plan(self, operation, params):
        r = self.request("GET", "https://api.fda.gov/download.json")
        data = r.data["results"]["drug"][self.endpoint]
        files = data["partitions"]
        if params.partition >= len(files):
            raise MolQuarryError(
                "invalid_parameters",
                "Partition is out of range",
                details={"partition_count": len(files)},
            )
        item = files[params.partition]
        return self.make_plan(
            operation,
            params,
            url=item["file"],
            filename=item["file"].rsplit("/", 1)[-1],
            format="zip",
            source_version=data.get("export_date"),
            provenance=[r.provenance],
            notes=[
                f"Partition {params.partition + 1} of {len(files)}; "
                "this is not a complete snapshot on its own."
            ],
        )


class DrugsFDA(OpenFDA):
    id = "drugsfda"
    endpoint = "drugsfda"


class OrangeBook(OpenFDA):
    id = "orangebook"
    endpoint = "orangebook"
    operations = {
        "search": Operation(
            "Search Orange Book products, listed patents and exclusivities",
            Search,
            {"query": 'products.active_ingredients.name:"ASPIRIN"', "limit": 5},
        )
    }
