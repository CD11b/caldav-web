import uuid
from datetime import datetime, timezone
import vobject
from caldav import DAVClient
from caldav.lib.error import NotFoundError, AuthorizationError, PropfindError
from models import Task, db, SyncLog
import logging
from typing import Optional, Dict, List, Any, Tuple
import time
from functools import wraps
import re

logger = logging.getLogger(__name__)

class CalDAVError(Exception):
    """Custom CalDAV exception for better error handling"""
    pass

class CalDAVConnectionError(CalDAVError):
    """Connection-related errors"""
    pass

class CalDAVAuthError(CalDAVError):
    """Authentication-related errors"""
    pass

class CalDAVTaskNotFoundError(CalDAVError):
    """Task not found errors"""
    pass

def retry_on_failure(max_retries: int = 3, delay: float = 1.0):
    """Decorator to retry CalDAV operations on failure"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError, PropfindError) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.warning(f"Attempt {attempt + 1} failed for {func.__name__}: {e}. Retrying in {delay}s...")
                        time.sleep(delay * (2 ** attempt))  # Exponential backoff
                    else:
                        logger.error(f"All {max_retries} attempts failed for {func.__name__}")
                except Exception as e:
                    # Don't retry on non-transient errors
                    raise e
            raise CalDAVConnectionError(f"Failed after {max_retries} attempts: {last_exception}")
        return wrapper
    return decorator

class CalDAVClient:
    def __init__(self, url: str, username: str, password: str, timeout: int = 30):
        """
        Initialize CalDAV client with improved error handling and validation
        
        Args:
            url: CalDAV server URL
            username: Username for authentication
            password: Password for authentication
            timeout: Request timeout in seconds
        """
        self.url = url
        self.username = username
        self.timeout = timeout
        self._client = None
        self._principal = None
        self._calendar_cache = {}
        self._connection_verified = False
        
        try:
            self._client = DAVClient(
                url, 
                username=username, 
                password=password,
                timeout=timeout
            )
            self._verify_connection()
            logger.info(f"CalDAV client initialized successfully for {url}")
        except AuthorizationError as e:
            raise CalDAVAuthError(f"Authentication failed: {e}")
        except Exception as e:
            raise CalDAVConnectionError(f"Failed to initialize CalDAV client: {e}")

    def _verify_connection(self):
        """Verify connection to CalDAV server"""
        try:
            self._principal = self._client.principal()
            self._connection_verified = True
        except Exception as e:
            self._connection_verified = False
            raise CalDAVConnectionError(f"Cannot connect to CalDAV server: {e}")

    def is_connected(self) -> bool:
        """Check if client is connected to server"""
        return self._connection_verified and self._client is not None

    @retry_on_failure(max_retries=3)
    def get_calendar_by_url(self, calendar_url: str):
        """
        Get calendar by URL with caching and improved error handling
        
        Args:
            calendar_url: URL of the calendar
            
        Returns:
            Calendar object
            
        Raises:
            CalDAVError: If calendar not found or connection issues
        """
        if not self.is_connected():
            raise CalDAVConnectionError("Client is not connected")
        
        # Check cache first
        if calendar_url in self._calendar_cache:
            return self._calendar_cache[calendar_url]
        
        try:
            calendars = self._principal.calendars()
            for calendar in calendars:
                if str(calendar.url) == calendar_url:
                    # Cache the calendar
                    self._calendar_cache[calendar_url] = calendar
                    return calendar
            
            raise CalDAVTaskNotFoundError(f"Calendar URL not found: {calendar_url}")
        except Exception as e:
            if isinstance(e, CalDAVError):
                raise
            raise CalDAVError(f"Failed to get calendar: {e}")

    def get_task_by_uid(self, uid: str, calendar_url: str):
        calendar = self.get_calendar_by_url(calendar_url)
        try:
            return calendar.todo_by_uid(uid)
        except NotFoundError:
            return None
        except Exception as e:
            logger.warning(f"Error finding task by UID {uid}: {e}")
            return None

    
    def _parse_vtodo_safely(self, todo_data: str) -> Optional[Dict[str, Any]]:
        """
        Safely parse VTODO data with comprehensive error handling
        
        Args:
            todo_data: Raw VTODO data string
            
        Returns:
            Dictionary with parsed task data or None if parsing fails
        """
        try:
            vcal = vobject.readOne(todo_data)
            vtodo = next((c for c in vcal.components() if c.name == "VTODO"), None)
            
            if not vtodo:
                logger.warning("No VTODO component found in calendar data")
                return None

            # Extract basic fields with safe attribute access
            def safe_get_value(obj, attr, default=None):
                try:
                    prop = getattr(obj, attr, None)
                    return prop.value if prop else default
                except (AttributeError, ValueError, TypeError):
                    return default

            task_data = {
                'uid': safe_get_value(vtodo, 'uid', str(uuid.uuid4())),
                'summary': safe_get_value(vtodo, 'summary', 'Untitled Task'),
                'description': safe_get_value(vtodo, 'description'),
                'due': safe_get_value(vtodo, 'due'),
                'created': safe_get_value(vtodo, 'created'),
                'completed_at': safe_get_value(vtodo, 'completed'),
                'priority': safe_get_value(vtodo, 'priority'),
                'percent_complete': safe_get_value(vtodo, 'percent_complete', 0),
            }

            # Handle status and completion
            status = safe_get_value(vtodo, 'status', 'NEEDS-ACTION')
            task_data['completed'] = status == "COMPLETED"

            # Handle parent relationship
            related_to = safe_get_value(vtodo, 'related_to')
            task_data['parent_uid'] = related_to if related_to else None

            # Validate and convert dates
            for date_field in ['due', 'created', 'completed_at']:
                if task_data[date_field]:
                    try:
                        if hasattr(task_data[date_field], 'astimezone'):
                            task_data[date_field] = task_data[date_field].astimezone(timezone.utc)
                        elif isinstance(task_data[date_field], datetime):
                            if task_data[date_field].tzinfo is None:
                                task_data[date_field] = task_data[date_field].replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid date format for {date_field}: {e}")
                        task_data[date_field] = None

            # Validate priority
            if task_data['priority'] is not None:
                try:
                    priority = int(task_data['priority'])
                    task_data['priority'] = max(1, min(9, priority))  # Clamp to 1-9 range
                except (ValueError, TypeError):
                    task_data['priority'] = None

            return task_data

        except Exception as e:
            logger.error(f"Failed to parse VTODO: {e}")
            return None

    @retry_on_failure(max_retries=2)
    def get_all_tasks(self, calendar_url: str) -> List[Dict[str, Any]]:
        """
        Get all tasks from calendar with improved error handling and validation
        
        Args:
            calendar_url: URL of the calendar
            
        Returns:
            List of task dictionaries
        """
        try:
            calendar = self.get_calendar_by_url(calendar_url)
            todos = calendar.todos()
            task_dicts = []
            parse_errors = 0

            logger.info(f"Fetching todos from calendar: {calendar_url}")
            
            for todo in todos:
                try:
                    task_data = self._parse_vtodo_safely(todo.data)
                    if task_data:
                        task_dicts.append(task_data)
                    else:
                        parse_errors += 1
                except Exception as e:
                    logger.warning(f"Failed to process todo: {e}")
                    parse_errors += 1

            logger.info(f"Successfully parsed {len(task_dicts)} tasks, {parse_errors} parse errors")
            return task_dicts

        except Exception as e:
            logger.error(f"Failed to get all tasks: {e}")
            raise CalDAVError(f"Failed to retrieve tasks: {e}")

    def _validate_task_hierarchy(self, task_map: Dict[str, Dict]) -> Dict[str, Dict]:
        """
        Validate and fix task hierarchy to prevent cycles and invalid references
        
        Args:
            task_map: Dictionary mapping UIDs to task data
            
        Returns:
            Validated task map with fixed hierarchy
        """
        fixed_count = 0
        
        # First pass: Remove references to non-existent parents
        for task in task_map.values():
            parent_uid = task.get('parent_uid')
            if parent_uid and parent_uid not in task_map:
                logger.warning(f"Task {task['uid']} references non-existent parent {parent_uid}")
                task['parent_uid'] = None
                fixed_count += 1

        # Second pass: Detect and break cycles
        def has_cycle(start_uid: str, current_uid: str, visited: set) -> bool:
            """Detect cycles in task hierarchy"""
            if current_uid is None:
                return False
            if current_uid == start_uid:
                return True
            if current_uid in visited:
                return False
            
            visited.add(current_uid)
            if current_uid not in task_map:
                return False
                
            parent_uid = task_map[current_uid].get('parent_uid')
            return has_cycle(start_uid, parent_uid, visited.copy())

        for uid, task in task_map.items():
            parent_uid = task.get('parent_uid')
            if parent_uid and has_cycle(uid, parent_uid, set()):
                logger.warning(f"Detected cycle involving task {uid}. Breaking cycle.")
                task['parent_uid'] = None
                fixed_count += 1

        if fixed_count > 0:
            logger.info(f"Fixed {fixed_count} hierarchy issues")
        
        return task_map

    def sync_tasks_to_db(self, calendar_url: str) -> Dict[str, Any]:
        """
        Sync tasks from CalDAV to database with comprehensive error handling
        
        Args:
            calendar_url: URL of the calendar to sync
            
        Returns:
            Dictionary with sync results and statistics
        """
        start_time = time.time()
        sync_stats = {
            'total_fetched': 0,
            'created': 0,
            'updated': 0,
            'errors': 0,
            'hierarchy_fixes': 0
        }

        try:
            # Fetch tasks from CalDAV
            tasks_from_caldav = self.get_all_tasks(calendar_url)
            sync_stats['total_fetched'] = len(tasks_from_caldav)
            
            if not tasks_from_caldav:
                logger.info("No tasks found in calendar")
                return {'count': 0, 'stats': sync_stats}

            # Create task map and validate hierarchy
            task_map = {task['uid']: task for task in tasks_from_caldav}
            task_map = self._validate_task_hierarchy(task_map)

            # Sync tasks to database
            for task_data in task_map.values():
                try:
                    db_task = Task.query.filter_by(
                        uid=task_data['uid'], 
                        calendar_url=calendar_url
                    ).first()
                    
                    if not db_task:
                        # Create new task
                        db_task = Task(
                            uid=task_data['uid'],
                            calendar_url=calendar_url
                        )
                        db.session.add(db_task)
                        sync_stats['created'] += 1
                        logger.debug(f"Creating new task: {task_data['uid']}")
                    else:
                        sync_stats['updated'] += 1
                        logger.debug(f"Updating existing task: {task_data['uid']}")

                    # Update task fields
                    db_task.summary = task_data.get('summary', '')
                    db_task.completed = task_data.get('completed', False)
                    db_task.parent_uid = task_data.get('parent_uid')
                    db_task.description = task_data.get('description')
                    db_task.due = task_data.get('due')
                    db_task.priority = task_data.get('priority')
                    
                    # Handle completion timestamp
                    if task_data.get('completed') and task_data.get('completed_at'):
                        db_task.completed_at = task_data['completed_at']
                    elif not task_data.get('completed'):
                        db_task.completed_at = None

                    # Mark as synced
                    db_task.mark_synced()

                except Exception as e:
                    logger.error(f"Failed to sync task {task_data.get('uid', 'unknown')}: {e}")
                    sync_stats['errors'] += 1

            # Commit changes
            db.session.commit()
            
            duration = time.time() - start_time
            logger.info(f"Sync completed in {duration:.2f}s: {sync_stats}")
            
            return {
                'count': sync_stats['created'] + sync_stats['updated'],
                'stats': sync_stats,
                'duration': duration
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Sync failed: {e}")
            raise CalDAVError(f"Synchronization failed: {e}")

    def _normalize_date_value(self, date_value):
        """
        Normalize date value to datetime object with proper error handling
        """
        if date_value is None:
            return None

        try:
            if isinstance(date_value, datetime):
                if date_value.tzinfo is None:
                    return date_value.replace(tzinfo=timezone.utc)
                return date_value.astimezone(timezone.utc)

            elif isinstance(date_value, (int, float)):
                if 0 <= date_value <= 2147483647:
                    return datetime.fromtimestamp(date_value, tz=timezone.utc)
                else:
                    logger.warning(f"Invalid timestamp value: {date_value}")
                    return None

            elif isinstance(date_value, str):
                try:
                    if 'Z' in date_value:
                        date_value = date_value.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(date_value)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)
                except Exception as e:
                    logger.warning(f"Could not parse date string: {date_value} - {e}")
                    return None

            else:
                logger.warning(f"Unsupported date type in _normalize_date_value: {type(date_value)} - {date_value}")
                return None

        except Exception as e:
            logger.warning(f"Error normalizing date value {date_value}: {e}")
            return None

    def _create_vtodo(self, summary: str, uid: str, parent_uid: Optional[str] = None, 
                    description: Optional[str] = None, due: Optional[datetime] = None, 
                    priority: Optional[int] = None) -> str:
        """
        Create VTODO object with improved validation and error handling
        """
        if not summary or not str(summary).strip():
            raise ValueError("Task summary cannot be empty")

        if not uid:
            uid = str(uuid.uuid4())

        # Validate and coerce priority
        if priority is not None:
            try:
                priority = int(priority)
                if not (1 <= priority <= 9):
                    logger.warning(f"Priority {priority} out of range, clamping to 1–9")
                    priority = max(1, min(9, priority))
            except (ValueError, TypeError):
                logger.warning(f"Invalid priority value: {priority}")
                priority = None

        # Sanitize due field
        if isinstance(due, int):
            if 0 < due < 2147483647:
                due = datetime.fromtimestamp(due, tz=timezone.utc)
            else:
                logger.warning(f"Invalid due int: {due}, skipping")
                due = None
        elif due and not isinstance(due, (datetime, float, str)):
            logger.warning(f"Unsupported due type: {type(due)} — skipping due")
            due = None

        def format_datetime_for_vtodo(dt):
            if dt is None:
                return None
            if not isinstance(dt, (datetime, str, float)):
                logger.warning(f"Invalid type in format_datetime_for_vtodo: {type(dt)}")
                return None
            dt = self._normalize_date_value(dt)
            return dt.replace(tzinfo=None) if dt else None

        try:
            vtodo = vobject.iCalendar()
            vtodo.add('vtodo')

            # Required fields
            vtodo.vtodo.add('uid').value = str(uid)
            vtodo.vtodo.add('summary').value = str(summary).strip()

            # Timestamps
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            vtodo.vtodo.add('dtstamp').value = now_utc
            vtodo.vtodo.add('created').value = now_utc
            vtodo.vtodo.add('status').value = 'NEEDS-ACTION'

            # Optional: parent
            if parent_uid:
                rel = vtodo.vtodo.add('related-to')
                rel.value = str(parent_uid)
                rel.params['RELTYPE'] = ['PARENT']

            # Optional: description
            if description and str(description).strip():
                vtodo.vtodo.add('description').value = str(description).strip()

            # Optional: due
            if due:
                due_formatted = format_datetime_for_vtodo(due)
                if due_formatted:
                    due_prop = vtodo.vtodo.add('due')
                    due_prop.value = due_formatted
                    due_prop.params['TZID'] = ['UTC']

            # Optional: priority
            if priority is not None:
                vtodo.vtodo.add('priority').value = int(priority)

            return vtodo.serialize()

        except Exception as e:
            logger.error(f"Exception during VTODO creation: {e}")
            raise CalDAVError(f"Failed to create VTODO: {e}")



    @retry_on_failure(max_retries=2)
    def create_task(self, summary: str, calendar_url: str, parent_uid: Optional[str] = None,
                   description: Optional[str] = None, due: Optional[datetime] = None,
                   priority: Optional[int] = None) -> str:
        """
        Create a new task with improved validation and error handling
        
        Args:
            summary: Task summary/title
            calendar_url: URL of target calendar
            parent_uid: Parent task UID (optional)
            description: Task description (optional)
            due: Due date (optional)
            priority: Priority 1-9 (optional)
            
        Returns:
            UID of created task
        """
        if not summary or not summary.strip():
            raise ValueError("Task summary cannot be empty")
        
        try:
            calendar = self.get_calendar_by_url(calendar_url)
            uid = str(uuid.uuid4())
            
            # Validate parent exists if specified
            if parent_uid:
                parent_task = self._find_task_by_uid(parent_uid, calendar)
                if not parent_task:
                    logger.warning(f"Parent task {parent_uid} not found, creating task without parent")
                    parent_uid = None
            
            vtodo_data = self._create_vtodo(summary, uid, parent_uid, description, due, priority)
            calendar.add_todo(vtodo_data)
            
            logger.info(f"Created task {uid}: {summary}")
            return uid
            
        except Exception as e:
            logger.error(f"Failed to create task: {e}")
            raise CalDAVError(f"Task creation failed: {e}")

    def _validate_and_fix_vtodo_properties(self, vtodo):
        """
        Validate and fix VTODO properties before serialization to prevent type errors.
        This specifically handles the integer priority issue and other similar problems.
        """
        properties_to_fix = []
        
        for child in vtodo.getChildren():
            if hasattr(child, 'value'):
                value = child.value
                prop_name = child.name.upper()
                
                # Handle None values - remove the property
                if value is None:
                    logger.warning(f"Removing {prop_name} with None value")
                    properties_to_fix.append((child, 'remove'))
                    continue
                
                # Define properties that should remain as specific types
                integer_properties = {'PRIORITY', 'PERCENT-COMPLETE', 'SEQUENCE'}
                datetime_properties = {'DUE', 'DTSTART', 'DTEND', 'CREATED', 'DTSTAMP', 'COMPLETED', 'LAST-MODIFIED'}
                
                # CRITICAL FIX: Convert ALL integer properties to strings for vobject serialization
                if prop_name in integer_properties:
                    if isinstance(value, int):
                        # Convert integer to string to prevent backslashEscape errors
                        properties_to_fix.append((child, 'update', str(value)))
                        logger.debug(f"Converting integer {prop_name} to string: {value} -> '{value}'")
                    elif not isinstance(value, str):
                        try:
                            # Try to convert to int first, then to string
                            int_value = int(value)
                            properties_to_fix.append((child, 'update', str(int_value)))
                            logger.debug(f"Converting {prop_name} to string: {value} -> '{int_value}'")
                        except (ValueError, TypeError):
                            logger.warning(f"Could not convert {prop_name} to int: {value}, removing property")
                            properties_to_fix.append((child, 'remove'))
                            continue
                            
                # Handle datetime properties
                elif prop_name in datetime_properties:
                    if not isinstance(value, datetime):
                        logger.warning(f"Non-datetime value in {prop_name}: {type(value)} - {value}")
                        if not hasattr(value, 'year'):  # Not a datetime-like object
                            properties_to_fix.append((child, 'remove'))
                            continue
                    # Datetime properties are fine as-is
                            
                # Handle all other properties - ensure they are strings
                else:
                    if not isinstance(value, str):
                        # Convert non-string values to strings to prevent replace() method errors
                        new_value = str(value)
                        properties_to_fix.append((child, 'update', new_value))
                        logger.debug(f"Converting {prop_name} to string: {type(value)} -> str: {repr(value)}")
        
        # Apply the fixes
        for fix in properties_to_fix:
            child = fix[0]
            action = fix[1]
            
            if action == 'remove':
                vtodo.remove(child)
            elif action == 'update':
                child.value = fix[2]
        
        logger.debug(f"Applied {len(properties_to_fix)} property fixes")
        return len(properties_to_fix) > 0


    def _set_priority_safely(self, vtodo, priority_value):
        """
        Safely set priority value to avoid serialization issues.
        This method ensures priority is handled correctly regardless of vobject version.
        """
        if priority_value is None:
            # Remove priority if it exists
            if hasattr(vtodo, 'priority'):
                vtodo.remove(vtodo.priority)
            return
        
        try:
            # Validate priority range
            priority_int = int(priority_value)
            priority_int = max(1, min(9, priority_int))
            
            # Remove existing priority property
            if hasattr(vtodo, 'priority'):
                vtodo.remove(vtodo.priority)
            
            # Add new priority property as STRING to avoid serialization issues
            priority_prop = vtodo.add('priority')
            priority_prop.value = str(priority_int)  # CRITICAL: Store as string
            
            logger.debug(f"Set priority as string: '{priority_int}'")
            
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid priority value: {priority_value} - {e}")


    @retry_on_failure(max_retries=2)
    def update_task(self, todo, summary=None, description=None, due=None, priority=None, parent_uid=None, completed=None):
        """
        Update an existing task's VTODO properties with proper type validation and safety.
        """
        try:
            vcal = vobject.readOne(todo.data)
            vtodo = vcal.vtodo
            changed = False

            # Summary - ensure it's a string
            if summary is not None:
                summary_str = str(summary).strip()
                if hasattr(vtodo, 'summary'):
                    vtodo.summary.value = summary_str
                else:
                    vtodo.add('summary').value = summary_str
                changed = True

            # Description - ensure it's a string
            if description is not None:
                description_str = str(description).strip() if description else ""
                if hasattr(vtodo, 'description'):
                    if description_str:
                        vtodo.description.value = description_str
                    else:
                        # Remove description if empty
                        vtodo.remove(vtodo.description)
                else:
                    if description_str:
                        vtodo.add('description').value = description_str
                changed = True

            # Related-to (parent task) - ensure it's a string
            if parent_uid is not None:
                # Remove existing related-to if present
                if hasattr(vtodo, 'related_to'):
                    vtodo.remove(vtodo.related_to)
                
                # Add new parent if provided
                if parent_uid:
                    rel = vtodo.add('related-to')
                    rel.value = str(parent_uid)
                    rel.params['RELTYPE'] = ['PARENT']
                changed = True

            # Due date - handle various input types and ensure proper datetime
            if due is not None:
                normalized_due = self._normalize_date_value(due)
                if normalized_due:
                    # Convert to UTC and remove timezone info for VTODO
                    utc_due = normalized_due.astimezone(timezone.utc).replace(tzinfo=None)
                    if hasattr(vtodo, 'due'):
                        vtodo.due.value = utc_due
                    else:
                        due_prop = vtodo.add('due')
                        due_prop.value = utc_due
                    changed = True

            # Priority - USE THE NEW SAFE METHOD
            if priority is not None:
                self._set_priority_safely(vtodo, priority)
                changed = True

            # Completion status - ensure it's a proper status string
            if completed is not None:
                status_str = 'COMPLETED' if completed else 'NEEDS-ACTION'
                if hasattr(vtodo, 'status'):
                    vtodo.status.value = status_str
                else:
                    vtodo.add('status').value = status_str
                
                # Handle completion timestamp
                if completed:
                    completed_time = datetime.now(timezone.utc).replace(tzinfo=None)
                    if hasattr(vtodo, 'completed'):
                        vtodo.completed.value = completed_time
                    else:
                        vtodo.add('completed').value = completed_time
                    
                    # Remove any existing percent-complete properties and add new one as STRING
                    for child in list(vtodo.getChildren()):
                        if child.name.upper() == 'PERCENT-COMPLETE':
                            vtodo.remove(child)
                    
                    percent_prop = vtodo.add('percent-complete')
                    percent_prop.value = "100"  # CRITICAL: String value for safety
                    
                else:
                    # Remove completion timestamp and reset percent
                    if hasattr(vtodo, 'completed'):
                        vtodo.remove(vtodo.completed)
                    
                    # Remove any existing percent-complete properties and add new one as STRING
                    for child in list(vtodo.getChildren()):
                        if child.name.upper() == 'PERCENT-COMPLETE':
                            vtodo.remove(child)
                    
                    percent_prop = vtodo.add('percent-complete')
                    percent_prop.value = "0"  # CRITICAL: String value for safety
                
                changed = True

            # Update last modified timestamp
            if changed:
                now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                if hasattr(vtodo, 'last_modified'):
                    vtodo.last_modified.value = now_utc
                else:
                    vtodo.add('last-modified').value = now_utc

            # CRITICAL: Apply property validation BEFORE serialization
            if changed:
                property_fixes_applied = self._validate_and_fix_vtodo_properties(vtodo)
                if property_fixes_applied:
                    logger.info("Applied property fixes before serialization")

            # Serialize and save if anything changed
            if changed:
                try:
                    # Enhanced debug logging before serialization
                    logger.debug("Properties after validation, before serialization:")
                    for child in vtodo.getChildren():
                        if hasattr(child, 'value'):
                            logger.debug(f"  {child.name}: {type(child.value)} = {repr(child.value)}")
                    
                    todo.data = vcal.serialize()
                    todo.save()
                    logger.info(f"Updated task {getattr(vtodo, 'uid', 'unknown').value if hasattr(vtodo, 'uid') else 'unknown'}")
                    
                except Exception as serialize_error:
                    logger.error(f"Serialization failed: {serialize_error}")
                    # Enhanced error logging
                    logger.error("Properties at time of serialization failure:")
                    for child in vtodo.getChildren():
                        if hasattr(child, 'value'):
                            logger.error(f"  {child.name}: {type(child.value)} = {repr(child.value)}")
                    
                    raise CalDAVError(f"Failed to serialize updated task: {serialize_error}")
            else:
                logger.debug("No changes made to task.")

        except Exception as e:
            logger.error(f"Failed to update task: {e}")
            raise CalDAVError(f"Task update failed: {e}")



    @retry_on_failure(max_retries=2)
    def delete_task(self, uid: str, calendar_url: str) -> bool:
        """
        Delete task with improved error handling
        
        Args:
            uid: Task UID
            calendar_url: Calendar URL
            
        Returns:
            True if deletion successful
        """
        try:
            calendar = self.get_calendar_by_url(calendar_url)
            task = self._find_task_by_uid(uid, calendar)
            
            if task:
                task.delete()
                logger.info(f"Deleted task {uid}")
                return True
            else:
                logger.warning(f"Task {uid} not found for deletion")
                return False
                
        except Exception as e:
            logger.error(f"Failed to delete task {uid}: {e}")
            raise CalDAVError(f"Task deletion failed: {e}")

    def _find_task_by_uid(self, uid: str, calendar) -> Optional[Any]:
        """
        Find task by UID with improved error handling
        
        Args:
            uid: Task UID
            calendar: Calendar object
            
        Returns:
            Task object or None if not found
        """
        try:
            return calendar.todo_by_uid(uid)
        except NotFoundError:
            return None
        except Exception as e:
            logger.warning(f"Error finding task by UID {uid}: {e}")
            return None

    @retry_on_failure(max_retries=2)
    def list_calendars(self) -> List[Dict[str, str]]:
        """
        List available calendars with enhanced metadata
        
        Returns:
            List of calendar dictionaries with metadata
        """
        try:
            calendars = []
            for calendar in self._principal.calendars():
                cal_info = {
                    "name": getattr(calendar, 'name', 'Unnamed Calendar'),
                    "url": str(calendar.url),
                    "display_name": getattr(calendar, 'display_name', None),
                    "description": getattr(calendar, 'description', None),
                    "color": getattr(calendar, 'color', None),
                }
                
                # Try to get task count
                try:
                    todos = calendar.todos()
                    cal_info["task_count"] = len(todos) if todos else 0
                except Exception as e:
                    logger.warning(f"Could not get task count for calendar {cal_info['name']}: {e}")
                    cal_info["task_count"] = None
                
                calendars.append(cal_info)
            
            return calendars
            
        except Exception as e:
            logger.error(f"Failed to list calendars: {e}")
            raise CalDAVError(f"Failed to list calendars: {e}")

    def create_task_with_subtasks(self, summary: str, subtask_titles: List[str], 
                                 calendar_url: str) -> str:
        """
        Create a task with subtasks in a single operation
        
        Args:
            summary: Parent task summary
            subtask_titles: List of subtask titles
            calendar_url: Calendar URL
            
        Returns:
            Parent task UID
        """
        try:
            # Create parent task
            parent_uid = self.create_task(summary, calendar_url)
            
            # Create subtasks
            for sub_summary in subtask_titles:
                if sub_summary and sub_summary.strip():
                    try:
                        self.create_task(sub_summary.strip(), calendar_url, parent_uid)
                    except Exception as e:
                        logger.error(f"Failed to create subtask '{sub_summary}': {e}")
            
            logger.info(f"Created task with {len(subtask_titles)} subtasks: {summary}")
            return parent_uid
            
        except Exception as e:
            logger.error(f"Failed to create task with subtasks: {e}")
            raise CalDAVError(f"Failed to create task with subtasks: {e}")

    def get_sync_status(self) -> Dict[str, Any]:
        """
        Get detailed sync status and health information
        
        Returns:
            Dictionary with sync status information
        """
        return {
            "connected": self.is_connected(),
            "server_url": self.url,
            "username": self.username,
            "calendar_cache_size": len(self._calendar_cache),
            "last_connection_check": datetime.now(timezone.utc).isoformat()
        }