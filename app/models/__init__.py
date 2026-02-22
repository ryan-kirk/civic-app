
from sqlalchemy import String, Integer, Date, Time, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ..db import Base

class Meeting(Base):
	__tablename__ = "meetings"

	meeting_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
	name: Mapped[str] = mapped_column(String, default="")
	date: Mapped[str] = mapped_column(String, default="")  # keep simple first; later use Date
	time: Mapped[str] = mapped_column(String, default="")
	location: Mapped[str] = mapped_column(String, default="")
	type_id: Mapped[int] = mapped_column(Integer, default=0)
	video_url: Mapped[str] = mapped_column(String, default="")

	agenda_items = relationship("AgendaItem", back_populates="meeting", cascade="all, delete-orphan")
	documents = relationship("Document", back_populates="meeting", cascade="all, delete-orphan")

class AgendaItem(Base):
	__tablename__ = "agenda_items"
	__table_args__ = (
		UniqueConstraint("meeting_id", "item_key", name="uq_meeting_item_key"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)

	section: Mapped[str] = mapped_column(String, default="")
	item_key: Mapped[str] = mapped_column(String, index=True)   # "6.17"
	title: Mapped[str] = mapped_column(Text, default="")

	topic: Mapped[str] = mapped_column(String, default="")      # later: zoning/other
	summary: Mapped[str] = mapped_column(Text, default="")      # later: LLM summary

	meeting = relationship("Meeting", back_populates="agenda_items")
	documents = relationship("Document", back_populates="agenda_item")

class Document(Base):
	__tablename__ = "documents"
	__table_args__ = (
		UniqueConstraint("meeting_id", "document_id", name="uq_meeting_document_id"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)
	agenda_item_id: Mapped[int | None] = mapped_column(ForeignKey("agenda_items.id"), nullable=True)

	document_id: Mapped[int] = mapped_column(Integer, index=True)   # 148134
	title: Mapped[str] = mapped_column(Text, default="")
	url: Mapped[str] = mapped_column(Text, default="")
	handle: Mapped[str] = mapped_column(String, default="")

	meeting = relationship("Meeting", back_populates="documents")
	agenda_item = relationship("AgendaItem", back_populates="documents")


class MeetingRawData(Base):
	__tablename__ = "meeting_raw_data"
	__table_args__ = (
		UniqueConstraint("meeting_id", name="uq_meeting_raw_data_meeting_id"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)
	meeting_data_json: Mapped[str] = mapped_column(Text, default="")
	meeting_documents_json: Mapped[str] = mapped_column(Text, default="")

	meeting = relationship("Meeting")


class MeetingMinutesMetadata(Base):
	__tablename__ = "meeting_minutes_metadata"
	__table_args__ = (
		UniqueConstraint("meeting_id", "document_id", name="uq_meeting_minutes_document"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.meeting_id"), index=True)
	document_id: Mapped[int] = mapped_column(Integer, index=True)
	title: Mapped[str] = mapped_column(Text, default="")
	url: Mapped[str] = mapped_column(Text, default="")
	detected_date: Mapped[str] = mapped_column(String, default="")
	page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
	text_excerpt: Mapped[str] = mapped_column(Text, default="")
	status: Mapped[str] = mapped_column(String, default="unknown")

	meeting = relationship("Meeting")
