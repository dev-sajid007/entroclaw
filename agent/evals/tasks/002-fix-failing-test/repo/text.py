import re


def slugify(title: str) -> str:
    """Lowercase, replace runs of non-alphanumerics with a single '-', strip leading/trailing '-'."""
    slug = re.sub(r"[^a-z0-9]", "-", title.lower())
    return slug
