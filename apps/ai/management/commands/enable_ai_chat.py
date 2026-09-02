"""Switch the assistant from rule-based answers to a hosted model.

    setx ANTHROPIC_API_KEY "sk-ant-..."        # once, then reopen the terminal
    python manage.py enable_ai_chat

The key itself is never stored here — only the *name* of the environment
variable to read it from. A key in the database is a key in every backup.
"""

import os

from django.core.management.base import BaseCommand

from ...models import AIProvider, AIProviderConfig

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_ENV = "ANTHROPIC_API_KEY"


class Command(BaseCommand):
    help = "Activate the Anthropic chat backend (or fall back to rule-based)."

    def add_arguments(self, parser):
        parser.add_argument("--model", default=DEFAULT_MODEL)
        parser.add_argument("--env", default=DEFAULT_ENV, help="Env var holding the key.")
        parser.add_argument("--max-tokens", type=int, default=1024)
        parser.add_argument("--timeout", type=int, default=30)
        parser.add_argument(
            "--disable",
            action="store_true",
            help="Go back to rule-based answers.",
        )

    def handle(self, *args, **options):
        if options["disable"]:
            AIProviderConfig.objects.update(is_active=False)
            self.stdout.write(self.style.SUCCESS("Assistant is rule-based again."))
            return

        env_name = options["env"]
        if not os.environ.get(env_name):
            self.stdout.write(
                self.style.WARNING(
                    f"{env_name} is not set in this process.\n"
                    "The provider will be configured, but every request will fall "
                    "back to the rule-based answer until the key is present."
                )
            )

        config, _ = AIProviderConfig.objects.update_or_create(
            name="anthropic-chat",
            defaults={
                "provider": AIProvider.ANTHROPIC,
                "model": options["model"],
                "api_key_env_name": env_name,
                "max_tokens": options["max_tokens"],
                "timeout_seconds": options["timeout"],
                "is_active": True,
            },
        )
        # Exactly one provider may be active, or `get_chat_backend` would pick
        # whichever row the database happened to return first.
        AIProviderConfig.objects.exclude(pk=config.pk).update(is_active=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"Assistant now uses {config.model} via {env_name}.\n"
                "Facts are still read from the database first; the model only "
                "phrases them and answers open questions."
            )
        )
