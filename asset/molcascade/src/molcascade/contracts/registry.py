"""Explicit registry for versioned data contracts."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType

from molcascade.contracts.base import DataContract
from molcascade.contracts.schemas import BUILTIN_CONTRACTS
from molcascade.errors import ContractError


class ContractRegistry:
    """A deterministic registry with structured duplicate/lookup failures."""

    def __init__(
        self,
        contracts: Iterable[DataContract] = (),
        *,
        frozen: bool = False,
    ) -> None:
        self._contracts: dict[str, DataContract] = {}
        self._frozen = False
        for contract in contracts:
            self.register(contract)
        self._frozen = frozen

    def register(self, contract: DataContract) -> None:
        """Register one contract; never replace an existing definition."""

        if self._frozen:
            raise ContractError(
                "contract registry is frozen",
                code="CONTRACT_REGISTRY_FROZEN",
            )
        if contract.id in self._contracts:
            raise ContractError(
                f"contract is already registered: {contract.id}",
                code="CONTRACT_DUPLICATE",
                context={"contract_id": contract.id},
            )
        self._contracts[contract.id] = contract

    def get(self, contract_id: str) -> DataContract:
        """Return a contract or raise a stable, user-facing lookup error."""

        try:
            return self._contracts[contract_id]
        except KeyError as error:
            raise ContractError(
                f"unknown data contract: {contract_id}",
                code="CONTRACT_NOT_FOUND",
                hint="Install or enable a plugin package that declares this contract.",
                context={"contract_id": contract_id},
            ) from error

    def snapshot(self) -> Mapping[str, DataContract]:
        """Return a detached, read-only registry view."""

        return MappingProxyType(dict(self._contracts))

    def __contains__(self, contract_id: object) -> bool:
        return contract_id in self._contracts

    def __getitem__(self, contract_id: str) -> DataContract:
        return self.get(contract_id)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._contracts))

    def __len__(self) -> int:
        return len(self._contracts)


DEFAULT_CONTRACT_REGISTRY = ContractRegistry(BUILTIN_CONTRACTS, frozen=True)
CONTRACTS = DEFAULT_CONTRACT_REGISTRY.snapshot()


def get_contract(contract_id: str) -> DataContract:
    """Look up a built-in contract by exact, versioned identifier."""

    return DEFAULT_CONTRACT_REGISTRY.get(contract_id)


__all__ = [
    "CONTRACTS",
    "DEFAULT_CONTRACT_REGISTRY",
    "ContractRegistry",
    "get_contract",
]
