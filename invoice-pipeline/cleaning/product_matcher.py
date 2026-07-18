"""
Resolves a raw product name string (as it appeared on an invoice) to a
canonical Product record, using:
  1. Exact alias lookup (fast path -- we've seen this exact raw string before)
  2. Fuzzy matching against existing canonical product names
  3. Creation of a new canonical product if nothing matches well enough

Ambiguous matches (scores between review_band_min_score and
fuzzy_match_min_score) are NOT auto-resolved -- they're logged as an
ingestion_errors row of type 'ambiguous_product_match' for a human to
confirm, and in the meantime treated as a new product so no data is lost.
"""
from typing import Optional, Tuple
from rapidfuzz import process, fuzz

from db.models import Product, ProductAlias
from pipeline.config_loader import load_thresholds


class ProductMatcher:
    def __init__(self, session):
        self.session = session
        thresholds = load_thresholds()["product_matching"]
        self.auto_match_score = thresholds["fuzzy_match_min_score"]
        self.review_band_score = thresholds["review_band_min_score"]

    def resolve(self, raw_name: str, category: Optional[str] = None) -> Tuple[Product, str]:
        """Returns (product, match_type) where match_type is one of:
        'exact_alias', 'fuzzy_auto', 'ambiguous_new', 'new_product'."""
        clean_name = raw_name.strip()

        # 1. Exact alias lookup
        alias = self.session.query(ProductAlias).filter_by(raw_name=clean_name).first()
        if alias:
            product = self.session.query(Product).get(alias.product_id)
            if product:
                return product, "exact_alias"

        # 2. Fuzzy match against existing canonical names
        existing_products = self.session.query(Product).all()
        if existing_products:
            # Compare case-insensitively so casing differences alone don't
            # cost match confidence; the displayed/stored name still keeps
            # whatever casing was first seen.
            choices = {p.product_id: p.product_name.lower() for p in existing_products}
            match = process.extractOne(
                clean_name.lower(), choices, scorer=fuzz.token_sort_ratio
            )
            if match:
                matched_name, score, matched_id = match
                if score >= self.auto_match_score:
                    product = self.session.query(Product).get(matched_id)
                    self._add_alias(clean_name, product.product_id)
                    return product, "fuzzy_auto"
                elif score >= self.review_band_score:
                    # Ambiguous: don't auto-merge. Create as new product,
                    # but caller should log this for manual review.
                    new_product = self._create_product(clean_name, category)
                    return new_product, "ambiguous_new"

        # 3. No reasonable match -- brand new product
        new_product = self._create_product(clean_name, category)
        return new_product, "new_product"

    def _create_product(self, name: str, category: Optional[str]) -> Product:
        product = Product(product_name=name, category=category)
        self.session.add(product)
        self.session.flush()  # get product_id without full commit
        self._add_alias(name, product.product_id)
        return product

    def _add_alias(self, raw_name: str, product_id: str):
        existing = self.session.query(ProductAlias).filter_by(raw_name=raw_name).first()
        if not existing:
            self.session.add(ProductAlias(raw_name=raw_name, product_id=product_id))
