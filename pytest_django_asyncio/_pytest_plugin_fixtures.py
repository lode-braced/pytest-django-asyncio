from __future__ import annotations

import contextlib
import inspect
import threading
import types
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
    django_db_blocker._async_allowed_thread_ids = frozenset(
        {main_thread_id, executor_thread_id}
    )
    try:
        yield
    finally:
        if previous_thread_ids is sentinel:
            delattr(django_db_blocker, "_async_allowed_thread_ids")
        else:
            django_db_blocker._async_allowed_thread_ids = previous_thread_ids


@contextlib.contextmanager
def _share_connections_between_allowed_threads(
    *,
    main_thread_id: int,
    executor_thread_id: int,
) -> Generator[None, None, None]:
    from django.db import connections

    allowed_thread_ids = frozenset({main_thread_id, executor_thread_id})
    connection_local = connections._connections
    shared_storage = types.SimpleNamespace()
    original_lock_storage = connection_local._lock_storage
    original_create_connection = connections.create_connection
    patched_wrappers: list[tuple[Any, Any]] = []

    def patch_wrapper(wrapper: Any) -> Any:
        if any(existing_wrapper is wrapper for existing_wrapper, _original in patched_wrappers):
            return wrapper

        original_validate_thread_sharing = wrapper.validate_thread_sharing

        def validate_thread_sharing(self: Any) -> None:
            if threading.get_ident() in allowed_thread_ids:
                return
            original_validate_thread_sharing()

        wrapper.validate_thread_sharing = types.MethodType(validate_thread_sharing, wrapper)
        patched_wrappers.append((wrapper, original_validate_thread_sharing))
        return wrapper

    @contextlib.contextmanager
    def _lock_storage() -> Generator[Any, None, None]:
        if threading.get_ident() in allowed_thread_ids:
            yield shared_storage
        else:
            with original_lock_storage() as storage:
                yield storage

    def create_connection(self: Any, alias: str) -> Any:
        return patch_wrapper(original_create_connection(alias))

    with original_lock_storage() as storage:
        for alias in connections:
            if hasattr(storage, alias):
                setattr(shared_storage, alias, patch_wrapper(getattr(storage, alias)))

    connection_local._lock_storage = _lock_storage
    connections.create_connection = types.MethodType(create_connection, connections)
    try:
        yield
    finally:
        connection_local._lock_storage = original_lock_storage
        connections.create_connection = original_create_connection
        for wrapper, original_validate_thread_sharing in reversed(patched_wrappers):
            wrapper.validate_thread_sharing = original_validate_thread_sharing


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
    else:
        request.getfixturevalue("_async_django_db_helper")
