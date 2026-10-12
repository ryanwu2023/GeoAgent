"""Run inside the released pod: keep identities/secrets, update only origin."""
from backend.integrations import PiChatSettings, save, settings

if __name__ == "__main__":
    cfg = settings()
    cfg["origin"] = "https://chat.lhotsetech.com"
    save(PiChatSettings(**cfg))
    print("PiChat runtime origin configured; existing identity and application key preserved")
