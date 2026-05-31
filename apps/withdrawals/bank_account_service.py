"""
Bank account service — handles bank resolution + NIN name match + account saving.

Public methods:
  resolve_and_match(user, bank_code, account_number)
      Look up the account name with the provider, then match against the user's
      NIN name. Returns the resolved data on success; raises BankAccountError
      with a structured code on failure.

  save_account(user, resolved_data)
      Persist a verified bank account. Sets is_default automatically.

  list_active(user)
      Return user's active (non-deleted) bank accounts.

  soft_delete(account, user)
      Mark an account inactive. Updates default if needed.

  set_default(account, user)
      Set this account as the default for the user.
"""
from dataclasses import dataclass
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from apps.kyc.models import BankAccount, KYCProfile
from apps.kyc.name_match import names_match
from apps.kyc.providers import  get_provider


class BankAccountError(Exception):
    """Raised when a bank account operation fails. Has a structured code."""
    def __init__(self, code: str, message: str, extra: Optional[dict] = None):
        self.code = code
        self.message = message
        self.extra = extra or {}
        super().__init__(message)


@dataclass
class ResolvedAccount:
    """Result of a successful bank resolution + name match."""
    bank_code: str
    bank_name: str
    account_number: str
    account_name: str
    nin_name: str    # for audit, never returned to client on mismatch


class BankAccountService:
    """Service for managing saved bank accounts in the withdrawals flow."""

    # ─── KYC gate ───────────────────────────────────────────────────────────

    @staticmethod
    def require_verified_kyc(user) -> KYCProfile:
        """
        Raises BankAccountError if the user hasn't completed NIN verification.
        Returns the KYCProfile on success.
        """
        try:
            profile = user.kyc_profile
        except KYCProfile.DoesNotExist:
            raise BankAccountError(
                'KYC_REQUIRED',
                'You must complete identity verification (NIN) before adding a bank account.',
            )

        if not profile.nin_verified:
            raise BankAccountError(
                'KYC_REQUIRED',
                'You must complete identity verification (NIN) before adding a bank account.',
            )

        if not profile.nin_full_name:
            raise BankAccountError(
                'KYC_NAME_MISSING',
                'Your verified identity name is missing. Please contact support.',
            )

        return profile

    # ─── Resolve & match ─────────────────────────────────────────────────────

    @staticmethod
    def resolve_and_match(
        user, bank_code: str, account_number: str,
    ) -> ResolvedAccount:
        """
        Look up the bank account's name via the provider, then match it
        against the user's NIN name.

        Raises BankAccountError on:
          - KYC_REQUIRED        no verified KYC
          - PROVIDER_ERROR      bank resolution failed (timeout / API error)
          - NAME_MISMATCH       resolved name doesn't match NIN name

        Returns ResolvedAccount on success.
        """
        profile = BankAccountService.require_verified_kyc(user)

        # Call provider
        try:
            provider = get_provider()
            result = provider.resolve_bank_account(
                bank_code=bank_code,
                account_number=account_number,
            )
        except KYCProviderError as e:
            raise BankAccountError(
                'PROVIDER_ERROR',
                f'Unable to verify the bank account: {e}',
            )

        resolved_name = result.account_name or ''
        bank_name = getattr(result, 'bank_name', '') or ''

        if not resolved_name:
            raise BankAccountError(
                'PROVIDER_ERROR',
                'Bank could not return an account name for that number.',
            )

        # Name match against NIN name
        if not names_match(profile.nin_full_name, resolved_name):
            raise BankAccountError(
                'NAME_MISMATCH',
                'The bank account does not appear to belong to you. '
                'Please use an account in your own name.',
                extra={'account_name': resolved_name},
            )

        return ResolvedAccount(
            bank_code=bank_code,
            bank_name=bank_name,
            account_number=account_number,
            account_name=resolved_name,
            nin_name=profile.nin_full_name,
        )

    # ─── Save / list / delete ───────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def save_account(user, resolved: ResolvedAccount) -> BankAccount:
        """
        Persist a verified bank account. Returns existing one if duplicate.
        Sets is_default=True on the saved (or reactivated) account.
        """
        # Look up an existing record (could be soft-deleted)
        existing = BankAccount.objects.filter(
            user=user,
            bank_code=resolved.bank_code,
            account_number=resolved.account_number,
        ).first()

        if existing:
            # Reactivate + refresh fields
            existing.is_active = True
            existing.bank_name = resolved.bank_name or existing.bank_name
            existing.account_name = resolved.account_name
            existing.verified_at = timezone.now()
            existing.save()
            account = existing
        else:
            account = BankAccount.objects.create(
                user=user,
                bank_code=resolved.bank_code,
                bank_name=resolved.bank_name,
                account_number=resolved.account_number,
                account_name=resolved.account_name,
                is_active=True,
                verified_at=timezone.now(),
            )

        # Set this one as the default — unset others
        BankAccount.objects.filter(user=user, is_active=True).update(is_default=False)
        account.is_default = True
        account.save(update_fields=['is_default'])

        return account

    @staticmethod
    def list_active(user) -> List[BankAccount]:
        return list(
            BankAccount.objects.filter(user=user, is_active=True)
                               .order_by('-is_default', '-verified_at', '-created_at')
        )

    @staticmethod
    @transaction.atomic
    def soft_delete(account: BankAccount, user) -> None:
        if account.user_id != user.id:
            raise BankAccountError('NOT_FOUND', 'Bank account not found.')
        was_default = account.is_default
        account.is_active = False
        account.is_default = False
        account.save(update_fields=['is_active', 'is_default'])

        # If we deleted the default, promote the next most recent active one
        if was_default:
            next_one = (
                BankAccount.objects.filter(user=user, is_active=True)
                                   .order_by('-verified_at', '-created_at')
                                   .first()
            )
            if next_one:
                next_one.is_default = True
                next_one.save(update_fields=['is_default'])

    @staticmethod
    @transaction.atomic
    def set_default(account: BankAccount, user) -> None:
        if account.user_id != user.id or not account.is_active:
            raise BankAccountError('NOT_FOUND', 'Bank account not found.')
        BankAccount.objects.filter(user=user, is_active=True).update(is_default=False)
        account.is_default = True
        account.save(update_fields=['is_default'])