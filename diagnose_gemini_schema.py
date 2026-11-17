"""One diagnostic action, at most two explicit Gemini requests, no business writes.

The input is a failed w1-after evaluation JSON containing response_json_schema.
Only its schema and model name are used; resident descriptions are not sent.
"""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from google import genai
from google.genai import types


PROMPT = "This is a schema compatibility test, not a property investigation. Return one JSON object following the provided schema. Use short test text and values allowed by the schema."
MINIMAL_SCHEMA = {
    "type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"],
}


def run_checks(client, model, full_schema, api_key):
    results = []
    for name, schema in (("minimal_schema", MINIMAL_SCHEMA), ("saved_full_schema", full_schema)):
        canonical = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        row = {"probe": name, "schema_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
               "schema_bytes": len(canonical.encode()), "api_accepted": False,
               "semantic_review": "not_applicable_diagnostic_only"}
        try:
            response = client.models.generate_content(
                model=model, contents=PROMPT,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_json_schema=schema,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    max_output_tokens=4096,
                ),
            )
            # HTTP acceptance is separate from complete output or business correctness.
            row["api_accepted"] = True
            row["finish_reasons"] = [str(candidate.finish_reason) for candidate in response.candidates or []]
            print(f"{name}: API accepted (not a business verification)")
        except Exception as error:
            message = getattr(error, "message", None)
            message = message if isinstance(message, str) else "SDK did not provide a structured error message."
            if api_key:
                message = message.replace(api_key, "[REDACTED]")
            row.update(error_type=type(error).__name__, http_code=getattr(error, "code", None),
                       api_status=getattr(error, "status", None), api_message=message[:4000])
            print(f"{name}: {row['error_type']} {row['http_code']} {row['api_status']}: {row['api_message']}")
        results.append(row)
        # A minimal-schema failure already answers the first diagnostic question.
        if name == "minimal_schema" and not row["api_accepted"]:
            print("Stopped after minimal request failure; no full-schema request or automatic retry.")
            break
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    bundle = json.loads(args.input.read_text(encoding="utf-8-sig"))
    schema = bundle.get("response_json_schema")
    model = bundle.get("model_name")
    if not isinstance(schema, dict) or not isinstance(model, str) or not model:
        parser.error("The input must be the failed w1-after JSON with a schema and model name.")
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        parser.error("GEMINI_API_KEY is missing in this terminal.")
    report = {
        "kind": "gemini-schema-probe-v1", "model_name": model,
        "source_output_schema_version": bundle.get("output_schema_version"),
        "diagnostic_prompt": PROMPT,
        "limits": "At most two requests, no automatic retries; no resident text, database or application source changes. API acceptance is not semantic acceptance.",
    }
    with genai.Client(api_key=api_key, vertexai=False, http_options=types.HttpOptions(
        timeout=20_000, retry_options=types.HttpRetryOptions(attempts=1),
    )) as client:
        report["probes"] = run_checks(client, model, schema, api_key)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = args.input.parent / f"schema-probe-{stamp}-{uuid4().hex[:8]}.json"
    with target.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(target.resolve())


if __name__ == "__main__":
    main()
