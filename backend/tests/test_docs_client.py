from unittest.mock import MagicMock, patch

from resume_tailor.docs_client import DocsClient


def _doc_with_paragraphs(*texts):
    return {
        "body": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": text}}]}}
                for text in texts
            ]
        }
    }


@patch("resume_tailor.docs_client.get_docs_service")
def test_get_paragraphs_strips_and_skips_blank(mock_get_service):
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    mock_service.documents.return_value.get.return_value.execute.return_value = _doc_with_paragraphs(
        "  First bullet  \n", "", "Second bullet"
    )

    client = DocsClient()
    paragraphs = client.get_paragraphs("doc-id")

    assert paragraphs == ["First bullet", "Second bullet"]


@patch("resume_tailor.docs_client.get_docs_service")
def test_count_occurrences(mock_get_service):
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    mock_service.documents.return_value.get.return_value.execute.return_value = _doc_with_paragraphs(
        "Built a pipeline", "Built another pipeline"
    )

    client = DocsClient()
    count = client.count_occurrences("doc-id", "Built")

    assert count == 2
