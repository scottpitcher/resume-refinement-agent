"""
Thin wrapper around the Docs API.

This is the piece that makes true in-place editing possible: replaceAllText
operates on the document's actual structure and preserves the formatting of
the matched text's surrounding run. Nothing is downloaded, unzipped, or
regenerated from scratch.
"""

from typing import List

from .auth import get_docs_service


class DocsClient:
    def __init__(self):
        self.service = get_docs_service()

    def get_document(self, document_id: str) -> dict:
        return self.service.documents().get(documentId=document_id).execute()

    def get_paragraphs(self, document_id: str) -> List[str]:
        """
        Returns the document's text content split into paragraphs, in order.
        Used to build the atomic fact record (one entry per resume line/bullet)
        that every edit gets checked against.
        """
        doc = self.get_document(document_id)
        paragraphs = []
        for element in doc.get("body", {}).get("content", []):
            para = element.get("paragraph")
            if not para:
                continue
            text = "".join(
                run.get("textRun", {}).get("content", "")
                for run in para.get("elements", [])
            ).strip()
            if text:
                paragraphs.append(text)
        return paragraphs

    def get_full_text(self, document_id: str) -> str:
        return "\n".join(self.get_paragraphs(document_id))

    def replace_text(self, document_id: str, old_text: str, new_text: str, match_case: bool = True) -> dict:
        """
        Replaces an exact text match in place. This is the only edit primitive
        the pipeline uses -- it preserves whatever formatting (bold, italics,
        font) already exists on the matched run. If old_text isn't an exact,
        unique match, this will either no-op or replace unintended occurrences,
        so callers should always pass the full original bullet text, not a
        fragment.
        """
        requests = [{
            "replaceAllText": {
                "containsText": {"text": old_text, "matchCase": match_case},
                "replaceText": new_text,
            }
        }]
        return self.service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()

    def count_occurrences(self, document_id: str, text: str) -> int:
        full_text = self.get_full_text(document_id)
        return full_text.count(text)

    def create_document(self, title: str) -> dict:
        return self.service.documents().create(body={"title": title}).execute()

    def append_text(self, document_id: str, text: str) -> dict:
        doc = self.get_document(document_id)
        end_index = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)
        requests = [{
            "insertText": {
                "location": {"index": end_index - 1},
                "text": text,
            }
        }]
        return self.service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
