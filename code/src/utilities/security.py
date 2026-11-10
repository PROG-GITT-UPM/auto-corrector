"""API authentication: a static bearer token (GRADER_API_TOKEN).

In the API docs (/docs), press **Authorize** and paste the token once to try every endpoint.
"""
import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False, description="The service's GRADER_API_TOKEN")


def require_token(request: Request,
                  credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    expected = request.app.state.settings.api_token
    if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API token",
                            headers={"WWW-Authenticate": "Bearer"})
