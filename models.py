from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, Text, Integer, Index
from sqlalchemy.orm import relationship, validates
from sqlalchemy.ext.hybrid import hybrid_property
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
import uuid

db = SQLAlchemy()

class Task(db.Model):
    __tablename__ = 'tasks'

    uid = Column(String(255), primary_key=True)
    summary = Column(Text, nullable=False)
    completed = Column(Boolean, default=False, nullable=False)
    parent_uid = Column(String(255), ForeignKey('tasks.uid'), nullable=True)
    calendar_url = Column(Text, nullable=False)

    # Enhanced datetime fields with timezone awareness
    due = Column(DateTime(timezone=True), nullable=True)
    created = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Enhanced task properties
    priority = Column(Integer, nullable=True)  # 1-9 scale, 1 being highest
    description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)  # JSON string of tags
    estimated_duration = Column(Integer, nullable=True)  # in minutes
    actual_duration = Column(Integer, nullable=True)  # in minutes

    # Sync tracking
    is_synced = Column(Boolean, default=True, nullable=False)
    operation = Column(String(50), nullable=True)  # 'create', 'update', 'delete'
    last_sync = Column(DateTime(timezone=True), nullable=True)
    sync_attempts = Column(Integer, default=0, nullable=False)

    # Relationships
    parent = relationship('Task', remote_side=[uid], backref='subtasks', lazy='select')

    # Database indexes for performance
    __table_args__ = (
        Index('idx_calendar_url', 'calendar_url'),
        Index('idx_parent_uid', 'parent_uid'),
        Index('idx_completed', 'completed'),
        Index('idx_due_date', 'due'),
        Index('idx_sync_status', 'is_synced', 'operation'),
    )

    @validates('summary')
    def validate_summary(self, key, summary):
        if not summary or not summary.strip():
            raise ValueError("Task summary cannot be empty")
        return summary.strip()

    @validates('priority')
    def validate_priority(self, key, priority):
        if priority is None:
            return None
        try:
            priority = int(priority)
        except (ValueError, TypeError):
            raise ValueError(f"Priority must be an integer, got {priority} ({type(priority)})")
        if not (1 <= priority <= 9):
            raise ValueError("Priority must be between 1 and 9")
        return priority


    @validates('uid')
    def validate_uid(self, key, uid):
        if not uid:
            return str(uuid.uuid4())
        return uid

    @hybrid_property
    def is_overdue(self):
        if not self.due or self.completed:
            return False
        
        # Handle timezone-naive datetime objects
        due_date = self.due
        if due_date.tzinfo is None:
            # If due date is timezone-naive, assume it's in UTC
            due_date = due_date.replace(tzinfo=timezone.utc)
        
        return due_date < datetime.now(timezone.utc)

    @hybrid_property
    def has_subtasks(self):
        return len(self.subtasks) > 0

    @hybrid_property
    def completion_percentage(self):
        if not self.subtasks:
            return 100 if self.completed else 0
        
        total_subtasks = len(self.subtasks)
        completed_subtasks = sum(1 for subtask in self.subtasks if subtask.completed)
        return int((completed_subtasks / total_subtasks) * 100)

    def mark_completed(self):
        """Mark task as completed with timestamp"""
        self.completed = True
        self.completed_at = datetime.now(timezone.utc)
        self.updated = datetime.now(timezone.utc)

    def mark_incomplete(self):
        """Mark task as incomplete"""
        self.completed = False
        self.completed_at = None
        self.updated = datetime.now(timezone.utc)

    def mark_for_sync(self, operation):
        """Mark task for synchronization"""
        self.is_synced = False
        self.operation = operation
        self.sync_attempts = 0
        self.updated = datetime.now(timezone.utc)

    def mark_synced(self):
        """Mark task as successfully synced"""
        self.is_synced = True
        self.operation = None
        self.last_sync = datetime.now(timezone.utc)
        self.sync_attempts = 0

    def increment_sync_attempts(self):
        """Increment sync attempt counter"""
        self.sync_attempts += 1

    def to_dict(self):
        """Convert task to dictionary for JSON serialization"""
        return {
            "uid": self.uid,
            "summary": self.summary,
            "completed": self.completed,
            "parent_uid": self.parent_uid,
            "description": self.description,
            "due": self.due.isoformat() if self.due else None,
            "created": self.created.isoformat() if self.created else None,
            "updated": self.updated.isoformat() if self.updated else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "priority": self.priority,
            "tags": self.tags,
            "estimated_duration": self.estimated_duration,
            "actual_duration": self.actual_duration,
            "is_synced": self.is_synced,
            "operation": self.operation,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "sync_attempts": self.sync_attempts,
            "is_overdue": self.is_overdue,
            "has_subtasks": self.has_subtasks,
            "completion_percentage": self.completion_percentage,
            "calendar_url": self.calendar_url
        }

    def __repr__(self):
        status = '✓' if self.completed else '○'
        overdue = '⚠' if self.is_overdue else ''
        sync_status = '↻' if not self.is_synced else ''
        return f"<Task {self.uid[:8]} {status}{overdue}{sync_status} {self.summary[:50]}>"

    def __str__(self):
        return f"{self.summary} ({'Completed' if self.completed else 'Pending'})"


class SyncLog(db.Model):
    """Track synchronization attempts and errors"""
    __tablename__ = 'sync_logs'

    id = Column(Integer, primary_key=True)
    calendar_url = Column(Text, nullable=False)
    operation = Column(String(50), nullable=False)  # 'sync', 'create', 'update', 'delete'
    task_uid = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False)  # 'success', 'error', 'warning'
    message = Column(Text, nullable=True)
    error_details = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index('idx_calendar_url_timestamp', 'calendar_url', 'timestamp'),
        Index('idx_task_uid', 'task_uid'),
    )

    def __repr__(self):
        return f"<SyncLog {self.operation} {self.status} {self.timestamp}>"


class Calendar(db.Model):
    """Store calendar metadata for caching and offline mode"""
    __tablename__ = 'calendars'

    url = Column(Text, primary_key=True)
    name = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    color = Column(String(7), nullable=True)  # Hex color code
    is_active = Column(Boolean, default=True, nullable=False)
    last_sync = Column(DateTime(timezone=True), nullable=True)
    created = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "url": self.url,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "color": self.color,
            "is_active": self.is_active,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "created": self.created.isoformat() if self.created else None,
            "updated": self.updated.isoformat() if self.updated else None
        }

    def __repr__(self):
        return f"<Calendar {self.name} ({self.url})>"