"""JSON as a type: what json.loads returns, and what json.dumps writes.

Containers are read-only (Mapping, Sequence), so a dict[str, str] or a list[int] is JSON as it stands. JsonObject
is the dict a to_dict builds.
"""

from collections.abc import Mapping, Sequence

type JsonValue = None | bool | int | float | str | Sequence[JsonValue] | Mapping[str, JsonValue]
type JsonObject = dict[str, JsonValue]
