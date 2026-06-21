"""
Gemini API client abstraction with backend switching.

Supports two backends controlled by AGENTOPS_GEMINI_BACKEND:
  - "aistudio" (default): google-generativeai SDK with GOOGLE_API_KEY.
  - "vertex": Vertex AI SDK (google-cloud-aiplatform) with Application Default
    Credentials (ADC).  Requires GOOGLE_CLOUD_PROJECT and
    GOOGLE_CLOUD_LOCATION (no API key needed).

Both backends expose the same interface:
    generate_text(prompt: str, model_id: str) -> str

The function returns an empty string on import errors or API errors (letting
callers apply their own fallback logic).

Lazy imports are used so that only the required SDK needs to be installed.
For example, a deployment using Vertex AI does not need google-generativeai,
and vice versa.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def generate_text(prompt: str, model_id: str, backend: str = "aistudio", **kwargs: object) -> str:
    """Call Gemini and return the text response.

    Args:
        prompt:   The text prompt to send.
        model_id: Gemini model identifier (e.g. "gemini-2.0-flash").
        backend:  "aistudio" or "vertex".  Reads GOOGLE_API_KEY or ADC
                  respectively.
        **kwargs: Extra keyword arguments forwarded to the backend:
                  - project (str)  — GCP project ID (vertex only)
                  - location (str) — GCP region (vertex only)
                  - api_key (str)  — API key override (aistudio only)

    Returns:
        The model's text response, or "" on any unrecoverable error.
    """
    if backend == "vertex":
        return _generate_vertex(prompt, model_id, **kwargs)
    return _generate_aistudio(prompt, model_id, **kwargs)


def _generate_aistudio(prompt: str, model_id: str, api_key: str = "", **_: object) -> str:
    """Generate text via AI Studio (google-generativeai SDK).

    Requires: pip install google-generativeai
    Auth:     GOOGLE_API_KEY environment variable (or api_key kwarg).
    """
    try:
        import google.generativeai as genai  # type: ignore[import-untyped]

        if api_key:
            genai.configure(api_key=api_key)

        model = genai.GenerativeModel(model_id)
        response = model.generate_content(prompt)
        return (response.text or "").strip()
    except ImportError:
        logger.error(
            "google-generativeai is not installed. "
            "Install it with: pip install google-generativeai"
        )
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI Studio generate_text failed (model=%s): %s", model_id, exc)
        return ""


def _generate_vertex(
    prompt: str,
    model_id: str,
    project: str = "",
    location: str = "us-central1",
    **_: object,
) -> str:
    """Generate text via Vertex AI (google-cloud-aiplatform / vertexai SDK).

    Requires: pip install google-cloud-aiplatform
    Auth:     Application Default Credentials (ADC).
              Run `gcloud auth application-default login` or use a service account.
    """
    try:
        import vertexai  # type: ignore[import-untyped]
        from vertexai.generative_models import GenerativeModel  # type: ignore[import-untyped]

        vertexai.init(project=project or None, location=location or "us-central1")
        model = GenerativeModel(model_id)
        response = model.generate_content(prompt)
        return (response.text or "").strip()
    except ImportError:
        logger.error(
            "google-cloud-aiplatform is not installed. "
            "Install it with: pip install google-cloud-aiplatform"
        )
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vertex AI generate_text failed (model=%s): %s", model_id, exc)
        return ""
