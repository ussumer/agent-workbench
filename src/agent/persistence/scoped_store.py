"""A Store view restricted to a single owner.

The Store itself is shared; this view is what makes "another user's file" an
error rather than an untested assumption. It wraps the real ``MongoDBStore``, so
what the agent reads through it is genuinely the persisted value.
"""

from __future__ import annotations

from typing import Any

from langgraph.store.base import BaseStore, Item, SearchOp

from agent.persistence.namespaces import assert_key_allowed, assert_namespace_allowed


class UserScopedStore:
    """Owner-scoped facade over a :class:`BaseStore`.

    Every call re-checks the namespace and key against the bound ``user_id``; a
    leaked reference to another user's view cannot read this user's data because
    the check is on the namespace, not on the caller.
    """

    def __init__(self, store: BaseStore, user_id: str) -> None:
        self._store = store
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id

    def _guard(self, namespace: tuple[str, ...], key: str | None = None) -> None:
        """Check the namespace, and the key when one is involved.

        Namespace-wide reads (``search``/``list_keys``) have no key to check; the
        results are filtered separately so a shared namespace cannot leak.
        """
        if key is None:
            assert_namespace_allowed(tuple(namespace), self._user_id)
        else:
            assert_key_allowed(tuple(namespace), key, self._user_id)

    def get(self, namespace: tuple[str, ...], key: str) -> Item | None:
        self._guard(namespace, key)
        return self._store.get(namespace, key)

    def put(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        index: bool | list[str] | None = None,
    ) -> None:
        self._guard(namespace, key)
        self._store.put(namespace, key, value, index)

    def delete(self, namespace: tuple[str, ...], key: str) -> None:
        self._guard(namespace, key)
        self._store.delete(namespace, key)

    def search(
        self,
        namespace: tuple[str, ...],
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchOp[Any]]:
        self._guard(namespace)
        return self._store.search(
            namespace, query=query, filter=filter, limit=limit, offset=offset
        )

    def list_keys(self, namespace: tuple[str, ...], *, limit: int = 100) -> list[str]:
        """Keys visible to this owner, filtered to their own prefix.

        LangGraph's Store has no "list keys" operation; this reads the
        namespace's items and keeps only those inside the owner's prefix, so a
        shared ``('skills',)`` namespace still cannot leak another user's files.
        """
        self._guard(namespace)
        items = self._store.search(namespace, limit=limit)
        return sorted(
            item.key
            for item in items
            if self._owns(namespace, item.key)
        )

    def _owns(self, namespace: tuple[str, ...], key: str) -> bool:
        try:
            self._guard(namespace, key)
        except PermissionError:
            return False
        return True

    # ---------------------------------------------------------------------- async
    #
    # DeepAgents' store backend prefers the async interface — deliberately, so a store read
    # does not block the event loop — and ``BaseStore``'s own ``aget``/``aput``/``adelete``/
    # ``asearch`` all funnel into ``abatch``, which the Mongo store implements natively. This
    # facade was written against the sync half only, so the first *async* touch of a store
    # mount raised ``AttributeError: 'UserScopedStore' object has no attribute 'aget'`` and
    # took the whole turn down. Found by T23: nothing earlier drove a real graph far enough to
    # reach the framework's async store path.
    #
    # Every async method repeats the same guard rather than reusing the sync twin's, because
    # reusing it would mean calling a blocking store from inside the loop — the very thing the
    # framework's async path exists to avoid. There is still one definition of "allowed":
    # ``_guard``.

    async def aget(
        self, namespace: tuple[str, ...], key: str, **kwargs: Any
    ) -> Item | None:
        self._guard(namespace, key)
        return await self._store.aget(namespace, key, **kwargs)

    async def aput(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        index: bool | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._guard(namespace, key)
        await self._store.aput(namespace, key, value, index, **kwargs)

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self._guard(namespace, key)
        await self._store.adelete(namespace, key)

    async def asearch(
        self,
        namespace: tuple[str, ...],
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
        **kwargs: Any,
    ) -> list[Any]:
        self._guard(namespace)
        return await self._store.asearch(
            namespace, query=query, filter=filter, limit=limit, offset=offset, **kwargs
        )

    async def abatch(self, ops: Any) -> list[Any]:  # noqa: ANN401 - protocol type
        """Guarded passthrough — the framework deletes through here, not through ``adelete``.

        ``StoreBackend.adelete`` builds ``PutOp(namespace, key, None)`` (a put of ``None`` is
        how this interface spells deletion), so guarding only ``adelete`` would leave the
        delete path unscoped. The ops are materialised first because a caller may hand over a
        generator, and iterating it once to check and again to run would consume it.
        """
        items = list(ops)
        for op in items:
            self._guard(tuple(op.namespace), getattr(op, "key", None))
        return await self._store.abatch(items)
