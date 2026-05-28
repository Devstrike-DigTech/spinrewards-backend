# """
# Challenge API views — player-facing only.

# Admin endpoints live in apps/admin_panel/challenge_views.py.

# Player endpoints (Telegram JWT required):
#   GET  /api/v1/challenges/                      List active visible challenges + my progress
#   GET  /api/v1/challenges/<challenge_id>/       Single challenge + my progress
# """
# import logging

# from django.db import models
# from django.utils import timezone
# from rest_framework import status
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework.views import APIView

# from .engine import ChallengeEngine
# from .models import Challenge, ChallengeProgress

# logger = logging.getLogger(__name__)


# def _get_user_progress(user, challenge: Challenge) -> ChallengeProgress:
#     """Get the user's current progress record for a challenge."""
#     window_start, _ = ChallengeEngine._get_window(challenge)
#     return ChallengeProgress.objects.filter(
#         user=user,
#         challenge=challenge,
#         window_start=window_start,
#     ).first()


# def serialize_challenge(challenge: Challenge, progress: ChallengeProgress = None) -> dict:
#     """Serialize a challenge with optional user progress. Used by both player + admin."""
#     data = {
#         'id': str(challenge.id),
#         'name': challenge.name,
#         'description': challenge.description,
#         'type': challenge.type,
#         'recurrence': challenge.recurrence,
#         'criteria': challenge.criteria,
#         'reward': challenge.reward,
#         'is_active': challenge.is_active,
#         'is_visible': challenge.is_visible,
#         'max_completions_per_user': challenge.max_completions_per_user,
#         'starts_at': challenge.starts_at.isoformat(),
#         'expires_at': challenge.expires_at.isoformat() if challenge.expires_at else None,
#         'created_at': challenge.created_at.isoformat(),
#         'participant_count': challenge.participant_count,
#         'completion_count': challenge.completion_count,
#     }

#     if progress:
#         data['my_progress'] = {
#             'current_count': progress.current_count,
#             'target_count': progress.target_count,
#             'progress_pct': progress.progress_pct,
#             'is_completed': progress.is_completed,
#             'completed_at': progress.completed_at.isoformat() if progress.completed_at else None,
#             'reward_claimed': progress.reward_claimed,
#             'reward_claimed_at': (
#                 progress.reward_claimed_at.isoformat()
#                 if progress.reward_claimed_at else None
#             ),
#             'window_start': progress.window_start.isoformat(),
#             'window_end': progress.window_end.isoformat() if progress.window_end else None,
#         }
#     else:
#         data['my_progress'] = None

#     return data


# class ChallengeListView(APIView):
#     """
#     GET /api/v1/challenges/

#     Returns all active visible challenges with the user's current progress.
#     """
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         now = timezone.now()
#         challenges = Challenge.objects.filter(
#             is_active=True,
#             is_visible=True,
#             starts_at__lte=now,
#         ).filter(
#             models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
#         ).order_by('created_at')

#         result = []
#         for challenge in challenges:
#             progress = _get_user_progress(request.user, challenge)
#             result.append(serialize_challenge(challenge, progress))

#         return Response({'success': True, 'data': {'challenges': result}})


# class ChallengeDetailView(APIView):
#     """
#     GET /api/v1/challenges/<challenge_id>/

#     Single challenge with the user's current progress.
#     """
#     permission_classes = [IsAuthenticated]

#     def get(self, request, challenge_id):
#         try:
#             challenge = Challenge.objects.get(
#                 id=challenge_id, is_active=True, is_visible=True,
#             )
#         except Challenge.DoesNotExist:
#             return Response(
#                 {'error': True, 'code': 'NOT_FOUND', 'message': 'Challenge not found.'},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         progress = _get_user_progress(request.user, challenge)
#         return Response({
#             'success': True,
#             'data': serialize_challenge(challenge, progress),
#         })

"""
Challenge API views — player-facing only.

Admin endpoints live in apps/admin_panel/challenge_views.py.

Player endpoints (Telegram JWT required):
  GET  /api/v1/challenges/                      List active visible challenges + my progress
  GET  /api/v1/challenges/<challenge_id>/       Single challenge + my progress
  POST /api/v1/challenges/<challenge_id>/claim/ Claim a completed challenge's reward
"""
import logging

from django.db import models
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .engine import ChallengeEngine, ClaimError
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
        # claimable = completed but not yet claimed
        claimable = progress.is_completed and not progress.reward_claimed
        data['my_progress'] = {
            'progress_id': str(progress.id),
            'current_count': progress.current_count,
            'target_count': progress.target_count,
            'progress_pct': progress.progress_pct,
            'is_completed': progress.is_completed,
            'completed_at': progress.completed_at.isoformat() if progress.completed_at else None,
            'claimable': claimable,
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


class ChallengeClaimView(APIView):
    """
    POST /api/v1/challenges/<challenge_id>/claim/

    Claim the reward for a completed challenge.

    The user must have completed the challenge (current_count >= target_count)
    and not yet claimed the reward.

    Success — 200:
    {
      "success": true,
      "data": {
        "message": "You earned 200 coins!",
        "reward_type": "coins",
        "amount": "200",
        "credited_to": "coin",
        "challenge_name": "Cee Cee"
      }
    }

    Failures — 400/404:
      NOT_FOUND        Challenge doesn't exist
      NO_PROGRESS      User hasn't started this challenge
      NOT_COMPLETED    Challenge not completed yet
      ALREADY_CLAIMED  Reward already claimed
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, challenge_id):
        try:
            challenge = Challenge.objects.get(id=challenge_id, is_active=True)
        except Challenge.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Challenge not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        progress = _get_user_progress(request.user, challenge)
        if not progress:
            return Response(
                {
                    'error': True,
                    'code': 'NO_PROGRESS',
                    'message': 'You have not started this challenge yet.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = ChallengeEngine.claim_reward(request.user, progress)
        except ClaimError as e:
            return Response(
                {'error': True, 'code': e.code, 'message': e.message},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build a friendly message
        rtype = result['reward_type']
        amount = result['amount']
        if rtype == 'coins':
            msg = f'You earned {amount} coins!'
        elif rtype == 'cash':
            msg = f'You earned ₦{amount}!'
        elif rtype == 'free_spins':
            msg = f'You earned {amount} free spins!'
        elif rtype == 'multiplier_boost':
            msg = f'You earned a {amount}x multiplier boost!'
        else:
            msg = 'Reward claimed!'

        return Response({
            'success': True,
            'data': {
                'message': msg,
                **result,
            },
        })