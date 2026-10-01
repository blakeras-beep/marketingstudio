"""Shared Jinja setup: filters, globals and the signed-in user on every page."""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import auth, config, fmt, images

HERE = Path(__file__).parent


def _context(request: Request) -> dict:
    user = auth.current_user(request)
    return {"user": user, "can_edit": auth.can_edit_inputs(user),
            "is_admin": bool(user) and user["role"] == "admin",
            "ROLE_LABELS": auth.ROLE_LABELS}


templates = Jinja2Templates(directory=HERE / "templates", context_processors=[_context])
templates.env.filters.update(fmt.FILTERS)
templates.env.globals.update(img=images.src, DASH=fmt.DASH, READY=fmt.READY, DISCLAIMER=config.DISCLAIMER,
                             city_line=fmt.city_line, plan_label=fmt.plan_label)
