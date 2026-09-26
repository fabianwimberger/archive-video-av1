"""AppState database model."""

from sqlalchemy import Column, String, Text
from app.database import Base


class AppState(Base):
    """Application state key-value store."""

    __tablename__ = "app_state"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
