"""Typed persistence failures."""


class StorageError(RuntimeError):
    pass


class StableIdConflict(StorageError):
    """A stable ID was replayed with different immutable content."""


class RecordNotFound(StorageError):
    pass


class OptimisticConcurrencyError(StorageError):
    pass


class InvalidPromotion(StorageError):
    pass


class SearchUnavailable(StorageError):
    pass

