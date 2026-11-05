from __future__ import annotations

import contextlib
import inspect
import threading
from typing import TYPE_CHECKING, Any

import pytest
from pytest_django.django_compat import is_django_unittest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator, Iterable
    from typing import Literal

    import django.test
    from pytest_django.plugin import DjangoDbBlocker

    _DjangoDbDatabases = Literal["__all__"] | Iterable[str] | None
    _DjangoDbAvailableApps = list[str] | None
    _DjangoDb = tuple[bool, bool, _DjangoDbDatabases, bool, _DjangoDbAvailableApps]


def install_async_only_unblock_patch(blocker_class: type[Any]) -> None:
    if "async_only" in inspect.signature(blocker_class.unblock).parameters:
        return

    def _unblocked_async_only(self: Any, wrapper_self: Any, *args: Any, **kwargs: Any) -> None:
        __tracebackhide__ = True
        from asgiref.sync import SyncToAsync

        allowed_thread_ids = getattr(self, "_async_allowed_thread_ids", None)
        if allowed_thread_ids is None:
            current_thread = threading.current_thread()
            is_allowed = any(
                thread is current_thread for thread in SyncToAsync.single_thread_executor._threads
            )
        else:
            is_allowed = threading.get_ident() in allowed_thread_ids

        if not is_allowed:
            raise RuntimeError(
                "Database access is only allowed in an async context, "
                "modify your test fixtures to be async or use the transactional_db fixture."
            )

        if self._real_ensure_connection is not None:
            self._real_ensure_connection(wrapper_self, *args, **kwargs)

    def unblock(self: Any, async_only: bool = False) -> Any:
        self._save_active_wrapper()
        if async_only:

            def _method(wrapper_self: Any, *args: Any, **kwargs: Any) -> None:
                return _unblocked_async_only(self, wrapper_self, *args, **kwargs)

            self._dj_db_wrapper.ensure_connection = _method
        else:
            self._dj_db_wrapper.ensure_connection = self._real_ensure_connection

        import pytest_django.plugin

        return pytest_django.plugin._DatabaseBlockerContextManager(self)

    blocker_class._unblocked_async_only = _unblocked_async_only
    blocker_class.unblock = unblock


@contextlib.contextmanager
def _allow_async_db_threads(
    django_db_blocker: DjangoDbBlocker,
    *,
    main_thread_id: int,
    executor_thread_id: int,
) -> Generator[None, None, None]:
    sentinel = object()
    previous_thread_ids = getattr(django_db_blocker, "_async_allowed_thread_ids", sentinel)
    django_db_blocker._async_allowed_thread_ids = frozenset({main_thread_id, executor_thread_id})
    try:
        yield
    finally:
        if previous_thread_ids is sentinel:
            delattr(django_db_blocker, "_async_allowed_thread_ids")
        else:
            django_db_blocker._async_allowed_thread_ids = previous_thread_ids


class _SharedConnections:
    def __init__(self, thread_ids: frozenset[int]) -> None:
        self.thread_ids = thread_ids
        self.by_alias: dict[str, Any] = {}
        self._shared_wrappers: list[Any] = []

    def set(self, alias: str, wrapper: Any) -> None:
        if not any(shared is wrapper for shared in self._shared_wrappers):
            wrapper.inc_thread_sharing()
            self._shared_wrappers.append(wrapper)
        self.by_alias[alias] = wrapper

    def release(self) -> None:
        for wrapper in self._shared_wrappers:
            wrapper.dec_thread_sharing()
        self._shared_wrappers.clear()


class _ConnectionsLocalProxy:
    """Stand-in for ``connections._connections`` (an ``asgiref.local.Local``) during a test.

    Threads in ``shared.thread_ids`` see the shared connections; every other thread keeps its
    own through the original ``Local``. Replacing the attribute avoids depending on ``Local``
    internals, which asgiref 3.13 rewrote. Django's ``BaseConnectionHandler`` only reaches it
    through getattr/setattr/delattr/hasattr, and ``django.test.signals`` swaps the same
    attribute on ``caches``.
    """

    __slots__ = ("_local", "_shared")

    def __init__(self, local: Any, shared: _SharedConnections) -> None:
        object.__setattr__(self, "_local", local)
        object.__setattr__(self, "_shared", shared)

    def __getattr__(self, alias: str) -> Any:
        if threading.get_ident() not in self._shared.thread_ids:
            return getattr(self._local, alias)
        try:
            return self._shared.by_alias[alias]
        except KeyError:
            raise AttributeError(alias) from None

    def __setattr__(self, alias: str, wrapper: Any) -> None:
        if threading.get_ident() not in self._shared.thread_ids:
            setattr(self._local, alias, wrapper)
        else:
            self._shared.set(alias, wrapper)

    def __delattr__(self, alias: str) -> None:
        if threading.get_ident() not in self._shared.thread_ids:
            delattr(self._local, alias)
            return
        try:
            del self._shared.by_alias[alias]
        except KeyError:
            raise AttributeError(alias) from None


@contextlib.contextmanager
def _share_connections_between_allowed_threads(
    main_thread_connections: dict[str, Any],
    *,
    main_thread_id: int,
    executor_thread_id: int,
) -> Generator[None, None, None]:
    from django.db import connections

    shared = _SharedConnections(frozenset({main_thread_id, executor_thread_id}))
    for alias, wrapper in main_thread_connections.items():
        shared.set(alias, wrapper)

    original_local = connections._connections
    connections._connections = _ConnectionsLocalProxy(original_local, shared)
    try:
        yield
    finally:
        connections._connections = original_local
        shared.release()


def _get_django_db_settings(request: pytest.FixtureRequest) -> Any:
    from pytest_django.fixtures import validate_django_db

    django_marker = request.node.get_closest_marker("django_db")
    if django_marker:
        (
            transactional,
            reset_sequences,
            databases,
            serialized_rollback,
            available_apps,
        ) = validate_django_db(django_marker)
    else:
        (
            transactional,
            reset_sequences,
            databases,
            serialized_rollback,
            available_apps,
        ) = False, False, None, False, None

    transactional = (
        transactional
        or reset_sequences
        or ("transactional_db" in request.fixturenames or "live_server" in request.fixturenames)
    )
    reset_sequences = reset_sequences or ("django_db_reset_sequences" in request.fixturenames)
    serialized_rollback = serialized_rollback or (
        "django_db_serialized_rollback" in request.fixturenames
    )
    return transactional, reset_sequences, databases, serialized_rollback, available_apps


def _build_pytest_django_test_case(
    test_case_class: type[django.test.TestCase],
    *,
    reset_sequences: bool,
    serialized_rollback: bool,
    databases: Any,
    available_apps: Any,
    skip_django_testcase_class_setup: bool,
) -> type[django.test.TestCase]:
    import django.test

    _reset_sequences = reset_sequences
    _serialized_rollback = serialized_rollback
    _databases = databases
    _available_apps = available_apps

    class PytestDjangoTestCase(test_case_class):
        reset_sequences = _reset_sequences
        serialized_rollback = _serialized_rollback
        if _databases is not None:
            databases = _databases
        if _available_apps is not None:
            available_apps = _available_apps

        if skip_django_testcase_class_setup:

            @classmethod
            def setUpClass(cls) -> None:
                super(django.test.TestCase, cls).setUpClass()

            @classmethod
            def tearDownClass(cls) -> None:
                super(django.test.TestCase, cls).tearDownClass()

    return PytestDjangoTestCase


@pytest.fixture
def _django_db_main_thread_connections(django_db_setup: None) -> dict[str, Any]:  # noqa: ARG001
    # Collected by a sync fixture because once the event loop runs on the main thread, asgiref
    # hands it a separate set of connections. Sharing these instead of opening new ones means
    # no per-test connection is left open (which would block dropping a Postgres test DB), and
    # Django closes them as usual when it tears down the test databases.
    from django.db import connections

    return {alias: connections[alias] for alias in connections}


@pytest.fixture
def _sync_django_db_helper(
    request: pytest.FixtureRequest,
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
) -> Generator[None, None, None]:
    from pytest_django import fixtures as pytest_django_fixtures

    yield from pytest_django_fixtures._django_db_helper.__wrapped__(
        request,
        django_db_setup,
        django_db_blocker,
    )


try:
    import pytest_asyncio
except ImportError:

    async def _async_django_db_helper(
        request: pytest.FixtureRequest,  # noqa: ARG001
        django_db_blocker: DjangoDbBlocker,  # noqa: ARG001
        _django_db_main_thread_connections: dict[str, Any],  # noqa: ARG001
    ) -> AsyncGenerator[None, None]:
        raise RuntimeError(
            "The `pytest-asyncio` plugin is required to use async Django database fixtures."
        )
        yield

else:

    @pytest_asyncio.fixture
    async def _async_django_db_helper(
        request: pytest.FixtureRequest,
        django_db_blocker: DjangoDbBlocker,
        _django_db_main_thread_connections: dict[str, Any],
    ) -> AsyncGenerator[None, None]:
        if is_django_unittest(request):
            yield
            return

        transactional, reset_sequences, databases, serialized_rollback, available_apps = (
            _get_django_db_settings(request)
        )

        import django.test
        from asgiref.sync import sync_to_async

        main_thread_id = threading.get_ident()
        executor_thread_id = await sync_to_async(threading.get_ident, thread_sensitive=True)()

        with _allow_async_db_threads(
            django_db_blocker,
            main_thread_id=main_thread_id,
            executor_thread_id=executor_thread_id,
        ):
            with _share_connections_between_allowed_threads(
                _django_db_main_thread_connections,
                main_thread_id=main_thread_id,
                executor_thread_id=executor_thread_id,
            ):
                with django_db_blocker.unblock(async_only=True):
                    test_case_class = (
                        django.test.TransactionTestCase if transactional else django.test.TestCase
                    )
                    pytest_django_test_case = _build_pytest_django_test_case(
                        test_case_class,
                        reset_sequences=reset_sequences,
                        serialized_rollback=serialized_rollback,
                        databases=databases,
                        available_apps=available_apps,
                        skip_django_testcase_class_setup=not transactional,
                    )

                    await sync_to_async(pytest_django_test_case.setUpClass)()

                    test_case = pytest_django_test_case(methodName="__init__")
                    await sync_to_async(test_case._pre_setup, thread_sensitive=True)()

                    yield

                    await sync_to_async(test_case._post_teardown, thread_sensitive=True)()
                    await sync_to_async(pytest_django_test_case.tearDownClass)()
                    await sync_to_async(pytest_django_test_case.doClassCleanups)()

    @pytest_asyncio.fixture
    async def _async_django_db_close_executor_connections() -> AsyncGenerator[None, None]:
        # Transactional async tests run their ORM calls on the sync_to_async executor thread
        # with that thread's own connections, which TransactionTestCase never closes. Left
        # open, they keep Postgres from dropping the test database at the end of the session.
        yield

        from asgiref.sync import sync_to_async
        from django.db import connections

        await sync_to_async(connections.close_all, thread_sensitive=True)()


@pytest.fixture
def _django_db_helper(
    request: pytest.FixtureRequest,
    django_db_setup: None,  # noqa: ARG001
    django_db_blocker: DjangoDbBlocker,  # noqa: ARG001
) -> None:
    asyncio_marker = request.node.get_closest_marker("asyncio")
    transactional, *_ = _get_django_db_settings(request)

    if transactional or not asyncio_marker:
        request.getfixturevalue("_sync_django_db_helper")
        if asyncio_marker:
            request.getfixturevalue("_async_django_db_close_executor_connections")
    else:
        request.getfixturevalue("_async_django_db_helper")
