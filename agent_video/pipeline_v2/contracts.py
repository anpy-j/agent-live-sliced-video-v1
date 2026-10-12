"""Small schemas. Text and duration are always reconstructed from source IDs."""

def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


def array(items, **kwargs):
    return {"type": "array", "items": items, **kwargs}


STRING = {"type": "string", "minLength": 1, "maxLength": 160}
IDS = array({"type": "integer", "minimum": 0}, minItems=1, maxItems=40)
BLOCK = obj({"ids": IDS, "topic": STRING, "fact": STRING})
EXTRACT = obj({"blocks": array(BLOCK),
               "fragments": array(obj({"ids": IDS, "need": STRING})),
               "rejected": array({"type": "integer", "minimum": 0})})
RESCUE = obj({"blocks": array(BLOCK)})
PLAN = obj({"selected": array(STRING, minItems=1), "reserve": array(STRING)})
ISSUE = obj({"block": STRING, "code": {"type": "string", "enum": [
    "incomplete", "unclear_reference", "changed_meaning", "chatter", "empty",
    "duplicate", "contradiction", "transition", "asr"]}, "reason": STRING})
REVIEW = obj({"issues": array(ISSUE)})


def validate(value, schema, path="$", depth=0):
    """Check native replies and caches alike, including extra fields and bool IDs."""
    if depth > 20:
        raise ValueError("返回结构嵌套过深")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]):
            raise ValueError(f"{path} 对象字段不符合契约")
        for key, spec in schema["properties"].items():
            validate(value[key], spec, path + "." + key, depth + 1)
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10000):
            raise ValueError(f"{path} 数组不符合契约")
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]", depth + 1)
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int) or value < schema.get("minimum", -1e99):
            raise ValueError(f"{path} 整数不合法")
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value.strip()) <= schema.get("maxLength", 100000):
            raise ValueError(f"{path} 文本不合法")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} 枚举不合法")
