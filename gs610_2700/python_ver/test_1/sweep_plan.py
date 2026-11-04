"""Partition endpoint-inclusive points into valid uniform Linear runs."""


def linear_runs(points):
    points = list(points)
    if len(points) < 3:
        return [points]
    differences = [points[i + 1] - points[i] for i in range(len(points) - 1)]
    if len(set(differences)) == 1:
        return [points]
    # A short endpoint step can be last (outbound) or first (return).
    if len(set(differences[:-1])) == 1:
        return [points[:-1], points[-1:]]
    if len(set(differences[1:])) == 1:
        return [points[:1], points[1:]]
    raise ValueError('扫描点无法拆分为等步长Linear扫描')
