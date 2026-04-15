from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

from .serializers import (
    TelegramAuthSerializer,
    TokenRefreshSerializer,
    UserSerializer,
)
from .services import AuthService


class TelegramAuthView(APIView):
    """POST /api/v1/auth/telegram — Authenticate via Telegram initData."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = TelegramAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user, tokens = AuthService.authenticate_telegram(
            serializer.validated_data['init_data']
        )

        return Response({
            'success': True,
            'data': {
                'access_token': tokens['access'],
                'refresh_token': tokens['refresh'],
                'token_type': 'Bearer',
                'user': UserSerializer(user).data,
            }
        }, status=status.HTTP_200_OK)


class TokenRefreshView(APIView):
    """POST /api/v1/auth/refresh — Refresh access token."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = AuthService.refresh_tokens(
            serializer.validated_data['refresh_token']
        )

        return Response({
            'success': True,
            'data': {
                'access_token': result['access'],
                'token_type': 'Bearer',
            }
        })


class MeView(APIView):
    """GET /api/v1/users/me — Get authenticated user profile."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            'success': True,
            'data': UserSerializer(request.user).data,
        })
