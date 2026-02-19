"""Sector Taxonomy 단위 테스트."""

from prime_jennie.domain.enums import SectorGroup
from prime_jennie.domain.sector_taxonomy import NAVER_TO_GROUP, get_sector_group


class TestSectorTaxonomy:
    def test_naver_to_group_has_entries(self):
        assert len(NAVER_TO_GROUP) > 70  # 79개 세분류

    def test_all_values_are_sector_group(self):
        for sector, group in NAVER_TO_GROUP.items():
            assert isinstance(group, SectorGroup), f"{sector} → {group} is not SectorGroup"

    def test_semiconductor(self):
        assert get_sector_group("반도체와반도체장비") == SectorGroup.SEMICONDUCTOR_IT

    def test_bio(self):
        assert get_sector_group("제약") == SectorGroup.BIO_HEALTH
        assert get_sector_group("생물공학") == SectorGroup.BIO_HEALTH

    def test_finance(self):
        assert get_sector_group("은행") == SectorGroup.FINANCE
        assert get_sector_group("증권") == SectorGroup.FINANCE

    def test_automobile(self):
        assert get_sector_group("자동차") == SectorGroup.AUTOMOBILE
        assert get_sector_group("자동차부품") == SectorGroup.AUTOMOBILE

    def test_construction(self):
        assert get_sector_group("건설") == SectorGroup.CONSTRUCTION

    def test_chemical(self):
        assert get_sector_group("화학") == SectorGroup.CHEMICAL
        assert get_sector_group("석유와가스") == SectorGroup.CHEMICAL

    def test_food_consumer(self):
        assert get_sector_group("식품") == SectorGroup.FOOD_CONSUMER
        assert get_sector_group("화장품") == SectorGroup.FOOD_CONSUMER

    def test_media(self):
        assert get_sector_group("게임엔터테인먼트") == SectorGroup.MEDIA_ENTERTAINMENT

    def test_transport(self):
        assert get_sector_group("해운사") == SectorGroup.LOGISTICS_TRANSPORT
        assert get_sector_group("항공사") == SectorGroup.LOGISTICS_TRANSPORT

    def test_telecom(self):
        assert get_sector_group("통신장비") == SectorGroup.TELECOM

    def test_utility(self):
        assert get_sector_group("전기유틸리티") == SectorGroup.UTILITY

    def test_unknown_returns_etc(self):
        assert get_sector_group("존재하지않는업종") == SectorGroup.ETC

    def test_all_14_groups_covered(self):
        """14개 대분류 모두 매핑에 포함."""
        groups = set(NAVER_TO_GROUP.values())
        for sg in SectorGroup:
            assert sg in groups, f"{sg} not in mapped groups"
