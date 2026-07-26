"""Pre-compose context collectors (chain activity, market data, social signals) fed to the writer."""

from app.modules.newspaper.writer_enrichment.gather import (
    WriterEnrichmentBundle,
    enrichment_block_for_row,
    format_enrichment_for_writer,
    gather_writer_enrichment,
)

__all__ = [
    "WriterEnrichmentBundle",
    "enrichment_block_for_row",
    "format_enrichment_for_writer",
    "gather_writer_enrichment",
]
