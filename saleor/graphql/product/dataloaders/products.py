from collections import defaultdict
from typing import DefaultDict, Dict, Iterable, List, Optional, Tuple

from django.db.models import F

from ....product import ProductMediaTypes
from ....product.models import (
    Category,
    Collection,
    CollectionChannelListing,
    CollectionProduct,
    Product,
    ProductChannelListing,
    ProductMedia,
    ProductType,
    ProductVariant,
    ProductVariantChannelListing,
    VariantMedia,
)
from ...core.dataloaders import DataLoader

ProductIdAndChannelSlug = Tuple[int, str]
VariantIdAndChannelSlug = Tuple[int, str]


class CategoryByIdLoader(DataLoader):
    context_key = "category_by_id"

    def batch_load(self, keys):
        categories = Category.objects.in_bulk(keys)
        return [categories.get(category_id) for category_id in keys]


class ProductByIdLoader(DataLoader):
    context_key = "product_by_id"

    def batch_load(self, keys):
        products = Product.objects.all().in_bulk(keys)
        return [products.get(product_id) for product_id in keys]


class ProductByVariantIdLoader(DataLoader):
    context_key = "product_by_variant_id"

    def batch_load(self, keys):
        def with_variants(variants):
            product_ids = [variant.product_id for variant in variants]
            return ProductByIdLoader(self.context).load_many(product_ids)

        return (
            ProductVariantByIdLoader(self.context).load_many(keys).then(with_variants)
        )


class ProductChannelListingByIdLoader(DataLoader[int, ProductChannelListing]):
    context_key = "productchannelisting_by_id"

    def batch_load(self, keys):
        product_channel_listings = ProductChannelListing.objects.in_bulk(keys)
        return [product_channel_listings.get(key) for key in keys]


class ProductChannelListingByProductIdLoader(DataLoader[int, ProductChannelListing]):
    context_key = "productchannelisting_by_product"

    def batch_load(self, keys):
        product_channel_listings = ProductChannelListing.objects.filter(
            product_id__in=keys
        )
        product_id_variant_channel_listings_map = defaultdict(list)
        for product_channel_listing in product_channel_listings:
            product_id_variant_channel_listings_map[
                product_channel_listing.product_id
            ].append(product_channel_listing)
        return [
            product_id_variant_channel_listings_map.get(product_id, [])
            for product_id in keys
        ]


class ProductChannelListingByProductIdAndChannelSlugLoader(
    DataLoader[ProductIdAndChannelSlug, ProductChannelListing]
):
    context_key = "productchannelisting_by_product_and_channel"

    def batch_load(self, keys):
        # Split the list of keys by channel first. A typical query will only touch
        # a handful of unique countries but may access thousands of product variants
        # so it's cheaper to execute one query per channel.
        product_channel_listing_by_channel: DefaultDict[str, List[int]] = defaultdict(
            list
        )
        for product_id, channel_slug in keys:
            product_channel_listing_by_channel[channel_slug].append(product_id)

        # For each channel execute a single query for all products.
        product_channel_listing_by_product_and_channel: DefaultDict[
            ProductIdAndChannelSlug, Optional[ProductChannelListing]
        ] = defaultdict()
        for channel_slug, product_ids in product_channel_listing_by_channel.items():
            product_channel_listings = self.batch_load_channel(
                channel_slug, product_ids
            )
            for product_id, product_channel_listing in product_channel_listings:
                product_channel_listing_by_product_and_channel[
                    (product_id, channel_slug)
                ] = product_channel_listing

        return [product_channel_listing_by_product_and_channel[key] for key in keys]

    def batch_load_channel(
        self, channel_slug: str, products_ids: Iterable[int]
    ) -> Iterable[Tuple[int, Optional[ProductChannelListing]]]:
        product_channel_listings = ProductChannelListing.objects.filter(
            channel__slug=channel_slug, product_id__in=products_ids
        )

        product_channel_listings_map: Dict[int, ProductChannelListing] = {}
        for product_channel_listing in product_channel_listings.iterator():
            product_channel_listings_map[
                product_channel_listing.product_id
            ] = product_channel_listing

        return [
            (products_id, product_channel_listings_map.get(products_id))
            for products_id in products_ids
        ]


class ProductTypeByIdLoader(DataLoader):
    context_key = "product_type_by_id"

    def batch_load(self, keys):
        product_types = ProductType.objects.in_bulk(keys)
        return [product_types.get(product_type_id) for product_type_id in keys]


class MediaByProductIdLoader(DataLoader):
    context_key = "media_by_product"

    def batch_load(self, keys):
        media = ProductMedia.objects.filter(product_id__in=keys)
        media_map = defaultdict(list)
        for media_obj in media:
            media_map[media_obj.product_id].append(media_obj)
        return [media_map[product_id] for product_id in keys]


class ImagesByProductIdLoader(DataLoader):
    context_key = "images_by_product"

    def batch_load(self, keys):
        images = ProductMedia.objects.filter(
            product_id__in=keys, type=ProductMediaTypes.IMAGE
        )
        images_map = defaultdict(list)
        for image in images:
            images_map[image.product_id].append(image)
        return [images_map[product_id] for product_id in keys]


class ProductVariantByIdLoader(DataLoader):
    context_key = "productvariant_by_id"

    def batch_load(self, keys):
        variants = ProductVariant.objects.in_bulk(keys)
        return [variants.get(key) for key in keys]


class ProductVariantsByProductIdLoader(DataLoader):
    context_key = "productvariants_by_product"

    def batch_load(self, keys):
        variants = ProductVariant.objects.filter(product_id__in=keys)
        variant_map = defaultdict(list)
        variant_loader = ProductVariantByIdLoader(self.context)
        for variant in variants.iterator():
            variant_map[variant.product_id].append(variant)
            variant_loader.prime(variant.id, variant)
        return [variant_map.get(product_id, []) for product_id in keys]


class ProductVariantsByProductIdAndChannel(DataLoader):
    context_key = "productvariant_by_product_and_channel"

    def batch_load(self, keys):
        product_ids, channel_slugs = zip(*keys)
        variants_filter = self.get_variants_filter(product_ids, channel_slugs)

        variants = ProductVariant.objects.filter(**variants_filter).annotate(
            channel_slug=F("channel_listings__channel__slug")
        )
        variant_map = defaultdict(list)
        for variant in variants.iterator():
            variant_map[(variant.product_id, variant.channel_slug)].append(variant)

        return [variant_map.get(key, []) for key in keys]

    def get_variants_filter(self, products_ids, channel_slugs):
        return {
            "product_id__in": products_ids,
            "channel_listings__channel__slug__in": [
                str(slug) for slug in channel_slugs
            ],
        }


class AvailableProductVariantsByProductIdAndChannel(
    ProductVariantsByProductIdAndChannel
):
    context_key = "available_productvariant_by_product_and_channel"

    def get_variants_filter(self, products_ids, channel_slugs):
        return {
            "product_id__in": products_ids,
            "channel_listings__channel__slug__in": [
                str(slug) for slug in channel_slugs
            ],
            "channel_listings__price_amount__isnull": False,
        }


class ProductVariantChannelListingByIdLoader(DataLoader):
    context_key = "productvariantchannelisting_by_id"

    def batch_load(self, keys):
        variants = ProductVariantChannelListing.objects.in_bulk(keys)
        return [variants.get(key) for key in keys]


class VariantChannelListingByVariantIdLoader(DataLoader):
    context_key = "productvariantchannelisting_by_productvariant"

    def batch_load(self, keys):
        variant_channel_listings = ProductVariantChannelListing.objects.filter(
            variant_id__in=keys
        )
        variant_id_variant_channel_listings_map = defaultdict(list)
        for variant_channel_listing in variant_channel_listings:
            variant_id_variant_channel_listings_map[
                variant_channel_listing.variant_id
            ].append(variant_channel_listing)
        return [
            variant_id_variant_channel_listings_map.get(variant_id, [])
            for variant_id in keys
        ]


class VariantChannelListingByVariantIdAndChannelLoader(
    DataLoader[VariantIdAndChannelSlug, ProductVariantChannelListing]
):
    context_key = "variantchannelisting_by_variant_and_channel"
    field = ""

    def batch_load(self, keys):
        # Split the list of keys by channel first. A typical query will only touch
        # a handful of unique countries but may access thousands of product variants
        # so it's cheaper to execute one query per channel.
        variant_channel_listing_by_channel: DefaultDict[str, List[int]] = defaultdict(
            list
        )
        for variant_id, channel in keys:
            variant_channel_listing_by_channel[channel].append(variant_id)

        # For each channel execute a single query for all product variants.
        variant_channel_listing_by_variant_and_channel: DefaultDict[
            VariantIdAndChannelSlug, Optional[ProductVariantChannelListing]
        ] = defaultdict()
        for channel, variant_ids in variant_channel_listing_by_channel.items():
            variant_channel_listings = self.batch_load_channel(channel, variant_ids)
            for variant_id, variant_channel_listing in variant_channel_listings:
                variant_channel_listing_by_variant_and_channel[
                    (variant_id, channel)
                ] = variant_channel_listing

        return [variant_channel_listing_by_variant_and_channel[key] for key in keys]

    def batch_load_channel(
        self, channel: str, variant_ids: Iterable[int]
    ) -> Iterable[Tuple[int, Optional[ProductVariantChannelListing]]]:
        filter = {
            f"channel__{self.field}": channel,
            "variant_id__in": variant_ids,
            "price_amount__isnull": False,
        }
        variant_channel_listings = ProductVariantChannelListing.objects.filter(**filter)

        variant_channel_listings_map: Dict[int, ProductVariantChannelListing] = {}
        for variant_channel_listing in variant_channel_listings.iterator():
            variant_channel_listings_map[
                variant_channel_listing.variant_id
            ] = variant_channel_listing

        return [
            (variant_id, variant_channel_listings_map.get(variant_id))
            for variant_id in variant_ids
        ]


class VariantChannelListingByVariantIdAndChannelSlugLoader(
    VariantChannelListingByVariantIdAndChannelLoader
):
    context_key = "variantchannelisting_by_variant_and_channelslug"
    field = "slug"


class VariantChannelListingByVariantIdAndChannelIdLoader(
    VariantChannelListingByVariantIdAndChannelLoader
):
    context_key = "variantchannelisting_by_variant_and_channelid"
    field = "id"


class VariantsChannelListingByProductIdAndChannelSlugLoader(
    DataLoader[ProductIdAndChannelSlug, Iterable[ProductVariantChannelListing]]
):
    context_key = "variantschannelisting_by_product_and_channel"

    def batch_load(self, keys):
        # Split the list of keys by channel first. A typical query will only touch
        # a handful of unique countries but may access thousands of product variants
        # so it's cheaper to execute one query per channel.
        variant_channel_listing_by_channel: DefaultDict[str, List[int]] = defaultdict(
            list
        )
        for product_id, channel_slug in keys:
            variant_channel_listing_by_channel[channel_slug].append(product_id)

        # For each channel execute a single query for all product variants.
        variant_channel_listing_by_product_and_channel: DefaultDict[
            ProductIdAndChannelSlug, Optional[Iterable[ProductVariantChannelListing]]
        ] = defaultdict()
        for channel_slug, product_ids in variant_channel_listing_by_channel.items():
            varaint_channel_listings = self.batch_load_channel(
                channel_slug, product_ids
            )
            for product_id, variants_channel_listing in varaint_channel_listings:
                variant_channel_listing_by_product_and_channel[
                    (product_id, channel_slug)
                ] = variants_channel_listing

        return [
            variant_channel_listing_by_product_and_channel.get(key, []) for key in keys
        ]

    def batch_load_channel(
        self, channel_slug: str, products_ids: Iterable[int]
    ) -> Iterable[Tuple[int, Optional[List[ProductVariantChannelListing]]]]:
        variants_channel_listings = ProductVariantChannelListing.objects.filter(
            channel__slug=channel_slug,
            variant__product_id__in=products_ids,
            price_amount__isnull=False,
        ).annotate(product_id=F("variant__product_id"))

        variants_channel_listings_map: Dict[
            int, List[ProductVariantChannelListing]
        ] = defaultdict(list)
        for variant_channel_listing in variants_channel_listings.iterator():
            variants_channel_listings_map[variant_channel_listing.product_id].append(
                variant_channel_listing
            )

        return [
            (products_id, variants_channel_listings_map.get(products_id, []))
            for products_id in products_ids
        ]


class ProductMediaByIdLoader(DataLoader):
    context_key = "product_media_by_id"

    def batch_load(self, keys):
        product_media = ProductMedia.objects.in_bulk(keys)
        return [product_media.get(product_media_id) for product_media_id in keys]


class ProductImageByIdLoader(DataLoader):
    context_key = "product_image_by_id"

    def batch_load(self, keys):
        images = ProductMedia.objects.filter(type=ProductMediaTypes.IMAGE).in_bulk(keys)
        return [images.get(product_image_id) for product_image_id in keys]


class MediaByProductVariantIdLoader(DataLoader):
    context_key = "media_by_product_variant"

    def batch_load(self, keys):
        variant_media = VariantMedia.objects.filter(variant_id__in=keys).values_list(
            "variant_id", "media_id"
        )

        variant_media_pairs = defaultdict(list)
        for variant_id, media_id in variant_media:
            variant_media_pairs[variant_id].append(media_id)

        def map_variant_media(variant_media):
            media_map = {media.id: media for media in variant_media}
            return [
                [media_map[media_id] for media_id in variant_media_pairs[variant_id]]
                for variant_id in keys
            ]

        return (
            ProductMediaByIdLoader(self.context)
            .load_many(set(media_id for variant_id, media_id in variant_media))
            .then(map_variant_media)
        )


class ImagesByProductVariantIdLoader(DataLoader):
    context_key = "images_by_product_variant"

    def batch_load(self, keys):
        variant_media = VariantMedia.objects.filter(
            variant_id__in=keys, media__type=ProductMediaTypes.IMAGE
        ).values_list("variant_id", "media_id")

        variant_media_pairs = defaultdict(list)
        for variant_id, media_id in variant_media:
            variant_media_pairs[variant_id].append(media_id)

        def map_variant_media(variant_media):
            media_map = {media.id: media for media in variant_media}
            return [
                [media_map[media_id] for media_id in variant_media_pairs[variant_id]]
                for variant_id in keys
            ]

        return (
            ProductMediaByIdLoader(self.context)
            .load_many(set(media_id for variant_id, media_id in variant_media))
            .then(map_variant_media)
        )


class CollectionByIdLoader(DataLoader):
    context_key = "collection_by_id"

    def batch_load(self, keys):
        collections = Collection.objects.in_bulk(keys)
        return [collections.get(collection_id) for collection_id in keys]


class CollectionsByProductIdLoader(DataLoader):
    context_key = "collections_by_product"

    def batch_load(self, keys):
        product_collection_pairs = list(
            CollectionProduct.objects.filter(product_id__in=keys)
            .order_by("id")
            .values_list("product_id", "collection_id")
        )
        product_collection_map = defaultdict(list)
        for pid, cid in product_collection_pairs:
            product_collection_map[pid].append(cid)

        def map_collections(collections):
            collection_map = {c.id: c for c in collections}
            return [
                [collection_map[cid] for cid in product_collection_map[pid]]
                for pid in keys
            ]

        return (
            CollectionByIdLoader(self.context)
            .load_many(set(cid for pid, cid in product_collection_pairs))
            .then(map_collections)
        )


class CollectionsByVariantIdLoader(DataLoader):
    context_key = "collections_by_variant"

    def batch_load(self, keys):
        def with_variants(variants):
            product_ids = [variant.product_id for variant in variants]
            return CollectionsByProductIdLoader(self.context).load_many(product_ids)

        return (
            ProductVariantByIdLoader(self.context).load_many(keys).then(with_variants)
        )


class ProductTypeByProductIdLoader(DataLoader):
    context_key = "producttype_by_product_id"

    def batch_load(self, keys):
        def with_products(products):
            product_ids = {p.id for p in products}
            product_types_map = ProductType.objects.filter(
                products__in=product_ids
            ).in_bulk()
            return [product_types_map[product.product_type_id] for product in products]

        return ProductByIdLoader(self.context).load_many(keys).then(with_products)


class ProductTypeByVariantIdLoader(DataLoader):
    context_key = "producttype_by_variant_id"

    def batch_load(self, keys):
        def with_variants(variants):
            product_ids = [v.product_id for v in variants]
            return ProductTypeByProductIdLoader(self.context).load_many(product_ids)

        return (
            ProductVariantByIdLoader(self.context).load_many(keys).then(with_variants)
        )


class CollectionChannelListingByIdLoader(DataLoader):
    context_key = "collectionchannelisting_by_id"

    def batch_load(self, keys):
        collections = CollectionChannelListing.objects.in_bulk(keys)
        return [collections.get(key) for key in keys]


class CollectionChannelListingByCollectionIdLoader(DataLoader):
    context_key = "collectionchannelisting_by_collection"

    def batch_load(self, keys):
        collections_channel_listings = CollectionChannelListing.objects.filter(
            collection_id__in=keys
        )
        collection_id_collection_channel_listings_map = defaultdict(list)
        for collection_channel_listing in collections_channel_listings:
            collection_id_collection_channel_listings_map[
                collection_channel_listing.collection_id
            ].append(collection_channel_listing)
        return [
            collection_id_collection_channel_listings_map.get(collection_id, [])
            for collection_id in keys
        ]


class CollectionChannelListingByCollectionIdAndChannelSlugLoader(DataLoader):
    context_key = "collectionchannelisting_by_collection_and_channel"

    def batch_load(self, keys):
        collection_ids = [key[0] for key in keys]
        channel_slugs = [key[1] for key in keys]
        collections_channel_listings = CollectionChannelListing.objects.filter(
            collection_id__in=collection_ids, channel__slug__in=channel_slugs
        ).annotate(channel_slug=F("channel__slug"))
        collections_channel_listings_by_collection_and_channel_map = {}
        for collections_channel_listing in collections_channel_listings:
            key = (
                collections_channel_listing.collection_id,
                collections_channel_listing.channel_slug,
            )
            collections_channel_listings_by_collection_and_channel_map[
                key
            ] = collections_channel_listing
        return [
            collections_channel_listings_by_collection_and_channel_map.get(key, None)
            for key in keys
        ]


class CategoryChildrenByCategoryIdLoader(DataLoader):
    context_key = "categorychildren_by_category"

    def batch_load(self, keys):
        categories = Category.objects.filter(parent__isnull=False)
        parent_to_children_mapping = defaultdict(list)
        for category in categories:
            parent_to_children_mapping[category.parent_id].append(category)

        return [parent_to_children_mapping.get(key, []) for key in keys]
