"""Small text helpers used by pkg.core."""


def slugify(text):
    keep = [c for c in text.lower() if c.isalnum() or c in " -"]
    return "".join(keep).replace(" ", "-")


def titleize(text):
    return " ".join(word.capitalize() for word in text.split())


def clamp(value, low, high):
    return max(low, min(value, high))


def humanize_bytes(num):
    for unit in ("B", "KB", "MB"):
        if num < 1024:
            return "%.1f%s" % (num, unit)
        num /= 1024.0
    return "%.1fGB" % num
