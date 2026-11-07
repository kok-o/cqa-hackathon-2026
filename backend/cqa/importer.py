"""Preserve source order and timestamps while adding stable message identifiers."""

from .models import Conversation, DatasetExport, Message


def normalize(export: DatasetExport) -> list[Conversation]:
    conversations = []
    for record in export.data.records:
        messages = [
            Message(
                **message.model_dump(),
                message_id=f"{record.conversation_id}:{index:04d}",
            )
            for index, message in enumerate(record.transcript)
        ]
        conversations.append(
            Conversation(
                **record.model_dump(exclude={"transcript"}),
                group_id=export.data.dataset_group_id,
                transcript=messages,
            )
        )
    return conversations
