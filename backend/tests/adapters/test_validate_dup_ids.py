"""Повтор id слоёв (модель нумерует L1… заново у каждого прибора) чинится, а не роняет статью."""
import pytest

from faktura.domain.records import Device, IssueRecord, Layer, PaperBundle, Publication
from faktura.ml.validate import validate_bundle


def test_layer_ids_repeated_per_device_are_renamed_issue_ids_cleared():
    p = "P011"
    b = PaperBundle(publication=Publication(publication_id=p),
                    devices=[Device(publication_id=p, device_id="DV1"), Device(publication_id=p, device_id="DV2")],
                    layers=[Layer(publication_id=p, layer_id="L1", device_id="DV1"),
                            Layer(publication_id=p, layer_id="L2", device_id="DV1"),
                            Layer(publication_id=p, layer_id="L1", device_id="DV2"),
                            Layer(publication_id=p, layer_id="L2", device_id="DV2")],
                    issues=[IssueRecord(publication_id=p, issue_id="I1", check_name="ambiguity"), IssueRecord(publication_id=p, issue_id="I1", check_name="ambiguity")])
    warnings: list[str] = []
    validate_bundle(b, warnings)
    assert [x.layer_id for x in b.layers] == ["DV1-L1", "DV1-L2", "DV2-L1", "DV2-L2"]
    assert [x.issue_id for x in b.issues] == ["I1", ""] and any("повтор" in w for w in warnings)


def test_repeated_device_id_still_fails():
    p = "P1"
    b = PaperBundle(publication=Publication(publication_id=p),
                    devices=[Device(publication_id=p, device_id="DV1"), Device(publication_id=p, device_id="DV1")])
    with pytest.raises(Exception, match="повторяется идентификатор DV1"):
        validate_bundle(b, [])
