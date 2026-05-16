"""
Challenge API views — player-facing only.

Admin endpoints live in apps/admin_panel/challenge_views.py.

Player endpoints (Telegram JWT required):
  GET  /api/v1/challenges/                      List active visible challenges + my progress
  GET  /api/v1/challenges/<challenge_id>/       Single challenge + my progress
"""
import logging

from django.db import models
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .engine import ChallengeEngine
from .models import Challenge, ChallengeProgress

logger = logging.getLogger(__name__)


def _get_user_progress(user, challenge: Challenge) -> ChallengeProgress:
    """Get the user's current progress record for a challenge."""
    window_start, _ = ChallengeEngine._get_window(challenge)
    return ChallengeProgress.objects.filter(
        user=user,
        challenge=challenge,
        window_start=window_start,
    ).first()


def serialize_challenge(challenge: Challenge, progress: ChallengeProgress = None) -> dict:
    """Serialize a challenge with optional user progress. Used by both player + admin."""
    data = {
        'id': str(challenge.id),
        'name': challenge.name,
        'description': challenge.description,
        'type': challenge.type,
        'recurrence': challenge.recurrence,
        'criteria': challenge.criteria,
        'reward': challenge.reward,
        'is_active': challenge.is_active,
        'is_visible': challenge.is_visible,
        'max_completions_per_user': challenge.max_completions_per_user,
        'starts_at': challenge.starts_at.isoformat(),
        'expires_at': challenge.expires_at.isoformat() if challenge.expires_at else None,
        'created_at': challenge.created_at.isoformat(),
        'participant_count': challenge.participant_count,
        'completion_count': challenge.completion_count,
    }

    if progress:
        data['my_progress'] = {
            'current_count': progress.current_count,
            'target_count': progress.target_count,
            'progress_pct': progress.progress_pct,
            'is_completed': progress.is_completed,
            'completed_at': progress.completed_at.isoformat() if progress.completed_at else None,
            'reward_claimed': progress.reward_claimed,
            'reward_claimed_at': (
                progress.reward_claimed_at.isoformat()
                if progress.reward_claimed_at else None
            ),
            'window_start': progress.window_start.isoformat(),
            'window_end': progress.window_end.isoformat() if progress.window_end else None,
        }
    else:
        data['my_progress'] = None

    return data


class ChallengeListView(APIView):
    """
    GET /api/v1/challenges/

    Returns all active visible challenges with the user's current progress.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        challenges = Challenge.objects.filter(
            is_active=True,
            is_visible=True,
            starts_at__lte=now,
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).order_by('created_at')

        result = []
        for challenge in challenges:
            progress = _get_user_progress(request.user, challenge)
            result.append(serialize_challenge(challenge, progress))

        return Response({'success': True, 'data': {'challenges': result}})


class ChallengeDetailView(APIView):
    """
    GET /api/v1/challenges/<challenge_id>/

    Single challenge with the user's current progress.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, challenge_id):
        try:
            challenge = Challenge.objects.get(
                id=challenge_id, is_active=True, is_visible=True,
            )
        except Challenge.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Challenge not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        progress = _get_user_progress(request.user, challenge)
        return Response({
            'success': True,
            'data': serialize_challenge(challenge, progress),
        })