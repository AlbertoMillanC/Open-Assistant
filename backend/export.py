import argparse
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from loguru import logger
from oasst_backend.database import engine
from oasst_backend.models import Message, MessageTreeState
from oasst_backend.models.message_tree_state import State as TreeState
from oasst_backend.utils import tree_export
from sqlmodel import Session, not_


def fetch_tree_ids(
    db: Session,
    state_filter: Optional[TreeState] = None,
    lang: Optional[str] = None,
) -> list[tuple[UUID, TreeState]]:
    tree_qry = (
        db.query(MessageTreeState)
        .select_from(MessageTreeState)
        .join(Message, MessageTreeState.message_tree_id == Message.id)
    )

    if lang is not None:
        tree_qry = tree_qry.filter(Message.lang == lang)

    if state_filter:
        tree_qry = tree_qry.filter(MessageTreeState.state == state_filter)

    return [(tree.message_tree_id, tree.state) for tree in tree_qry]


def fetch_tree_messages(
    db: Session,
    message_tree_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    deleted: bool = None,
    prompts_only: bool = False,
    lang: Optional[str] = None,
    review_result: Optional[bool] = None,
) -> List[Message]:
    qry = db.query(Message)

    if message_tree_id:
        qry = qry.filter(Message.message_tree_id == message_tree_id)
    if user_id:
        qry = qry.filter(Message.user_id == user_id)
    if deleted is not None:
        qry = qry.filter(Message.deleted == deleted)
    if prompts_only:
        qry = qry.filter(Message.parent_id.is_(None))
    if lang:
        qry = qry.filter(Message.lang == lang)
    if review_result is False:
        qry = qry.filter(not_(Message.review_result), Message.review_count > 2)
    elif review_result is True:
        qry = qry.filter(Message.review_result)

    return qry.all()


def export_trees(
    db: Session,
    export_file: Optional[Path] = None,
    use_compression: bool = False,
    deleted: bool = False,
    user_id: Optional[UUID] = None,
    prompts_only: bool = False,
    state_filter: Optional[TreeState] = None,
    lang: Optional[str] = None,
    review_result: Optional[bool] = None,
) -> None:
    trees_to_export: List[tree_export.ExportMessageTree] = []

    if user_id or review_result is False:
        messages = fetch_tree_messages(
            db,
            user_id=user_id,
            deleted=deleted,
            prompts_only=prompts_only,
            lang=lang,
            review_result=review_result,
        )
        tree_export.write_messages_to_file(export_file, messages, use_compression)
    else:
        message_tree_ids = fetch_tree_ids(db, state_filter, lang=lang)

        message_trees = [
            fetch_tree_messages(
                db,
                message_tree_id=tree_id,
                deleted=deleted,
                prompts_only=prompts_only,
                lang=None,
                review_result=review_result,
            )
            for (tree_id, _) in message_tree_ids
        ]

        # when exporting only-deleted we don't have a porper tree structure, export as list
        if deleted is True:
            messages = [m for t in message_trees for m in t]
            tree_export.write_messages_to_file(export_file, messages, use_compression)
        else:
            for (message_tree_id, message_tree_state), message_tree in zip(message_tree_ids, message_trees):
                t = tree_export.build_export_tree(message_tree_id, message_tree_state, message_tree)
                if prompts_only:
                    t.prompt.replies = None
                trees_to_export.append(t)

            tree_export.write_trees_to_file(export_file, trees_to_export, use_compression)


def validate_args(args):
    if args.deleted_only:
        args.include_deleted = True

    args.use_compression = args.export_file is not None and ".gz" in args.export_file

    if args.state and args.user is not None:
        raise ValueError("Cannot use --state when specifying a user ID")

    if args.export_file is None:
        logger.warning("No export file provided, output will be sent to STDOUT")


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--export-file",
        type=str,
        help="Name of file to export trees to. If not provided, output will be sent to STDOUT",
    )
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help="Include deleted messages in export",
    )
    parser.add_argument(
        "--deleted-only",
        action="store_true",
        help="Export only deleted messages (implies --include-deleted)",
    )
    parser.add_argument(
        "--include-spam",
        action="store_true",
        help="Export only messages with negative review result.",
    )
    parser.add_argument(
        "--spam-only",
        action="store_true",
        help="Export only messages with negative review result (implies --include-spam).",
    )
    parser.add_argument(
        "--user",
        type=str,
        help="Only export trees involving the user with the specified ID. Incompatible with --state.",
    )
    parser.add_argument(
        "--state",
        type=str,
        help="all|prompt_lottery_waiting|growing|ready_for_export|aborted_low_grade|halted_by_moderator|backlog_ranking",
    )
    parser.add_argument(
        "--lang",
        type=str,
        help="Filter message trees by language code (BCP 47)",
    )
    parser.add_argument(
        "--prompts-only",
        action="store_true",
        help="Export a list of initial prompt messages",
    )

    args = parser.parse_args()
    return args


def main():
    args = parse_args()
    validate_args(args)

    state_filter: Optional[TreeState] = None
    if args.state is None:
        state_filter = TreeState.READY_FOR_EXPORT
    elif args.state != "all":
        state_filter = TreeState(args.state)

    deleted: Optional[bool] = False
    if args.include_deleted:
        deleted = None
    if args.deleted_only:
        deleted = True

    review_result: Optional[bool] = True
    if args.include_spam:
        review_result = None
    if args.spam_only:
        review_result = False

    with Session(engine) as db:
        export_trees(
            db,
            Path(args.export_file) if args.export_file is not None else None,
            args.use_compression,
            deleted=deleted,
            user_id=UUID(args.user) if args.user is not None else None,
            prompts_only=args.prompts_only,
            state_filter=state_filter,
            lang=args.lang,
            review_result=review_result,
        )


if __name__ == "__main__":
    main()
