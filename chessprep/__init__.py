"""ChessPrep - szukanie praktycznie trudnych wariantów debiutowych."""

__version__ = "0.4.0"

try:
    # Używaj magazynu certyfikatów systemu (np. gdy antywirus skanuje HTTPS).
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass
