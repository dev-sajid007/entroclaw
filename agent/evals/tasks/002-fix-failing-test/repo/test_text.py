from text import slugify


def test_simple():
    assert slugify("Hello World") == "hello-world"


def test_collapses_and_strips():
    assert slugify("  Hello,   World!  ") == "hello-world"
