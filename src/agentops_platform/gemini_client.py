"""
Gemini API client abstraction with backend switching.

Supports two backends controlled by AGENTOPS_GEMINI_BACKEND, both served by
the unified ``google-genai`` SDK (``from google import genai``):

  - "aistudio" (default):
      ``genai.Client(api_key=<GOOGLE_API_KEY>)``
      Suitable for individual developers and quick prototyping.

  - "vertex":
      ``genai.Client(vertexai=True, project=<PROJECT>, location=<LOCATION>)``
      Uses Application Default Credentials (ADC).  No API key needed.
      Suitable for production deployments on Google Cloud / Gemini Enterprise
      Agent Platform (formerly Vertex AI).

Both backends expose the same interface:
    generate_text(prompt: str, model_id: str) -> str

The function returns an empty string on import errors or API errors (letting
callers apply their own fallback logic).

The ``google-genai`` package (PyPI: google-genai, import: ``from google import
genai``) is the single unified SDK covering both AI Studio and the Gemini
Enterprise Agent Platform (Vertex AI API).
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
    """Generate text via AI Studio using the unified google-genai SDK.

    Requires: pip install google-genai
    Auth:     GOOGLE_API_KEY environment variable (or api_key kwarg).
              The SDK also auto-picks up GOOGLE_API_KEY from the environment
              when api_key is not supplied explicitly.
    """
    try:
        from google import genai  # type: ignore[import-untyped]

        client = genai.Client(api_key=api_key if api_key else None)
        response = client.models.generate_content(model=model_id, contents=prompt)
        text: str = response.text or ""
        return text.strip()
    except ImportError:
        logger.error(
            "google-genai is not installed. "
            "Install it with: pip install google-genai"
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
    """Generate text via the Gemini Enterprise Agent Platform using the unified google-genai SDK.

    Formerly known as Vertex AI; now accessible via the same google-genai SDK
    with ``vertexai=True``.

    Requires: pip install google-genai
    Auth:     Application Default Credentials (ADC).
              Run ``gcloud auth application-default login`` or use a service account.
    """
    try:
        from google import genai  # type: ignore[import-untyped]

        client = genai.Client(
            vertexai=True,
            project=project or None,
            location=location or "us-central1",
        )
        response = client.models.generate_content(model=model_id, contents=prompt)
        text: str = response.text or ""
        return text.strip()
    except ImportError:
        logger.error(
            "google-genai is not installed. "
            "Install it with: pip install google-genai"
        )
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Gemini Enterprise Agent Platform generate_text failed (model=%s): %s",
            model_id,
            exc,
        )
        return ""
