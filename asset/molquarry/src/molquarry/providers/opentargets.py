from pydantic import Field

from ..errors import MolQuarryError
from ..models import InputModel
from .base import Operation, Page, Provider

URL = "https://api.platform.opentargets.org/api/v4/graphql"


class TargetInput(InputModel):
    ensembl_id: str = Field(pattern=r"^ENSG[0-9]{11}$")


class SearchInput(InputModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)
    page: int = Field(default=0, ge=0)


class DiseaseInput(TargetInput):
    limit: int = Field(default=20, ge=1, le=100)
    page: int = Field(default=0, ge=0)


class OpenTargets(Provider):
    id = "opentargets"
    operations = {
        "target": Operation(
            "Target annotation and tractability by Ensembl gene ID.",
            TargetInput,
            {"ensembl_id": "ENSG00000146648"},
        ),
        "search": Operation(
            "Search target, disease and drug entities.", SearchInput, {"query": "EGFR", "limit": 5}
        ),
        "diseases": Operation(
            "Ranked disease associations; scores are evidence aggregation scores.",
            DiseaseInput,
            {"ensembl_id": "ENSG00000146648", "limit": 5},
        ),
    }

    def query(self, operation, params):
        if operation == "search":
            query = """query($q:String!,$index:Int!,$size:Int!){
                search(queryString:$q,page:{index:$index,size:$size}){
                    total hits{id entity name description}}} """
            variables = {"q": params.query, "index": params.page, "size": params.limit}
        elif operation == "diseases":
            query = """query($id:String!,$index:Int!,$size:Int!){
                target(ensemblId:$id){associatedDiseases(page:{index:$index,size:$size}){
                    count rows{score disease{id name}}}}} """
            variables = {"id": params.ensembl_id, "index": params.page, "size": params.limit}
        else:
            query = """query($id:String!){target(ensemblId:$id){
                id approvedSymbol approvedName biotype tractability{label modality value}}} """
            variables = {"id": params.ensembl_id}
        response = self.request("POST", URL, body={"query": query, "variables": variables})
        data = response.data["data"]
        if operation != "search" and data.get("target") is None:
            raise MolQuarryError("not_found", "Open Targets target not found", source=self.id)
        if operation == "target":
            return Page([data["target"]], [response.provenance])
        section = data["search"] if operation == "search" else data["target"]["associatedDiseases"]
        rows = section["hits"] if operation == "search" else section["rows"]
        total = section["total"] if operation == "search" else section["count"]
        next_params = None
        if rows and (params.page + 1) * params.limit < total:
            next_params = {**params.model_dump(), "page": params.page + 1}
        return Page(rows, [response.provenance], total, next_params)
