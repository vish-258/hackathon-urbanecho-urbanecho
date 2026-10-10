"""Allow only the two schema layouts supported by deployment packaging."""


def migration_search_path(value: str = "public") -> str:
    names = tuple(name.strip() for name in value.split(","))
    if names not in {("public",), ("public", "extensions")}:
        raise ValueError("MIGRATION_SEARCH_PATH must be public or public,extensions")
    return ", ".join(names)
