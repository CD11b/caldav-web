from flask import Flask, request, jsonify, render_template, abort
from werkzeug.exceptions import BadRequest, NotFound, InternalServerError
from config import CALDAV_URL, CALDAV_USERNAME, CALDAV_PASSWORD, SQLALCHEMY_DATABASE_URI
from models import db, Task, SyncLog, Calendar
from caldav_client import CalDAVClient, CalDAVError
import logging
from datetime import datetime, timezone
import uuid
from dateutil.parser import parse
import json
from functools import wraps
import time

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JSON_SORT_KEYS'] = False

# Enhanced logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

db.init_app(app)

# Initialize database and CalDAV client
with app.app_context():
    db.create_all()

try:
    client = CalDAVClient(CALDAV_URL, CALDAV_USERNAME, CALDAV_PASSWORD)
    logger.info("CalDAV client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize CalDAV client: {e}")
    client = None

# Utility decorators and functions
def require_calendar_url(f):
    """Decorator to ensure calendar_url is provided"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        calendar_url = request.args.get("calendar_url") or (
            request.get_json(silent=True) or {}
        ).get("calendar_url")
        
        if not calendar_url:
            return jsonify({'error': 'Missing calendar_url parameter'}), 400
        return f(calendar_url, *args, **kwargs)
    return decorated_function

def validate_json_request(required_fields=None):
    """Decorator to validate JSON requests"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not request.is_json:
                return jsonify({'error': 'Content-Type must be application/json'}), 400
            
            data = request.get_json()
            if data is None:
                return jsonify({'error': 'Invalid JSON data'}), 400
            
            if required_fields:
                missing_fields = [field for field in required_fields if field not in data]
                if missing_fields:
                    return jsonify({
                        'error': f'Missing required fields: {", ".join(missing_fields)}'
                    }), 400
            
            return f(data, *args, **kwargs)
        return decorated_function
    return decorator

def log_sync_operation(calendar_url, operation, task_uid=None, status='success', message=None, error_details=None):
    """Log synchronization operations"""
    if not operation:
        logger.warning(f"Skipping log due to missing operation for task {task_uid}")
        return
    try:
        sync_log = SyncLog(
            calendar_url=calendar_url,
            operation=operation,
            task_uid=task_uid,
            status=status,
            message=message,
            error_details=error_details
        )
        db.session.add(sync_log)
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to log sync operation: {e}")


def handle_caldav_error(func, *args, **kwargs):
    """Handle CalDAV operations with proper error handling"""
    try:
        return func(*args, **kwargs), None
    except Exception as e:
        error_msg = str(e)
        logger.warning(f"CalDAV operation failed: {error_msg}")
        return None, error_msg

# Error handlers
@app.errorhandler(BadRequest)
def handle_bad_request(error):
    return jsonify({'error': 'Bad request', 'message': str(error)}), 400

@app.errorhandler(NotFound)
def handle_not_found(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(InternalServerError)
def handle_internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Check database connectivity
        db.session.execute('SELECT 1')
        db_status = 'connected'
    except Exception as e:
        db_status = f'error: {str(e)}'
    
    # Check CalDAV connectivity
    caldav_status = 'connected' if client and client.is_connected() else 'disconnected'
    
    return jsonify({
        'status': 'healthy',
        'database': db_status,
        'caldav': caldav_status,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

@app.route('/sync', methods=['POST'])
@require_calendar_url
def sync_tasks(calendar_url):
    """Sync tasks from CalDAV server"""
    if not client:
        return jsonify({'error': 'CalDAV client not available'}), 503
    
    try:
        start_time = time.time()
        sync_result = client.sync_tasks_to_db(calendar_url)
        sync_duration = time.time() - start_time
        
        log_sync_operation(
            calendar_url, 
            'sync', 
            status='success',
            message=f'Synced {sync_result.get("count", 0)} tasks in {sync_duration:.2f}s'
        )
        
        return jsonify({
            'status': 'synced',
            'tasks_synced': sync_result.get('count', 0),
            'duration': f'{sync_duration:.2f}s'
        })
    except Exception as e:
        logger.exception("Sync failed")
        log_sync_operation(calendar_url, 'sync', status='error', error_details=str(e))
        return jsonify({'error': 'Sync failed', 'details': str(e)}), 500

@app.route('/tasks', methods=['GET'])
@require_calendar_url
def get_tasks(calendar_url):
    """Get all tasks for a calendar with optional filtering"""
    # Optional sync before fetching
    if client and request.args.get('sync', '').lower() == 'true':
        try:
            client.sync_tasks_to_db(calendar_url)
        except Exception as e:
            logger.warning(f"Auto-sync failed: {e}")

    # Build query with filters
    query = Task.query.filter_by(calendar_url=calendar_url)
    
    # Filter by completion status
    completed = request.args.get('completed')
    if completed is not None:
        query = query.filter_by(completed=completed.lower() == 'true')
    
    # Filter by parent (top-level tasks only)
    if request.args.get('top_level_only', '').lower() == 'true':
        query = query.filter(Task.parent_uid.is_(None))
    
    # Filter by overdue tasks
    if request.args.get('overdue_only', '').lower() == 'true':
        # Get all tasks with due dates that are not completed first
        base_query = query.filter(
            Task.due.isnot(None),
            Task.completed == False
        )
        
        # Get all tasks and filter overdue ones in Python due to timezone handling complexity
        all_tasks_with_due = base_query.all()
        overdue_uids = []
        now_utc = datetime.now(timezone.utc)
        
        for task in all_tasks_with_due:
            due_date = task.due
            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)
            if due_date < now_utc:
                overdue_uids.append(task.uid)
        
        if overdue_uids:
            query = Task.query.filter_by(calendar_url=calendar_url).filter(Task.uid.in_(overdue_uids))
        else:
            # Return empty query if no overdue tasks
            query = Task.query.filter_by(calendar_url=calendar_url).filter(Task.uid == None)
    
    # Pagination
    page = int(request.args.get('page', 1))
    per_page = min(int(request.args.get('per_page', 100)), 500)  # Max 500 per page
    
    paginated_tasks = query.paginate(
        page=page, 
        per_page=per_page, 
        error_out=False
    )
    
    return jsonify({
        'tasks': [task.to_dict() for task in paginated_tasks.items],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': paginated_tasks.total,
            'pages': paginated_tasks.pages,
            'has_next': paginated_tasks.has_next,
            'has_prev': paginated_tasks.has_prev
        }
    })

@app.route('/tasks', methods=['POST'])
@validate_json_request(['summary'])
def create_task(data):
    """Create a new task"""
    calendar_url = data.get("calendar_url")
    if not calendar_url:
        return jsonify({"error": "Missing calendar_url"}), 400
    
    try:
        # Extract and validate task data
        summary = data.get("summary", "").strip()
        if not summary:
            return jsonify({"error": "Task summary cannot be empty"}), 400
        
        parent_uid = data.get("parent_uid")
        description = data.get("description")
        priority = data.get("priority")
        tags = data.get("tags")
        estimated_duration = data.get("estimated_duration")
        
        # Parse due date
        due = None
        if data.get("due"):
            try:
                due = parse(data["due"])
            except Exception:
                return jsonify({"error": "Invalid 'due' datetime format"}), 400
        
        # Validate priority
        if priority is not None:
            try:
                priority = int(priority)
                if priority < 0 or priority > 9:
                    return jsonify({"error": "Priority must be between 0 and 9 "}), 400
            except ValueError:
                return jsonify({"error": "Priority must be a number"}), 400
        
        # Validate parent task exists
        if parent_uid:
            parent_task = Task.query.filter_by(uid=parent_uid, calendar_url=calendar_url).first()
            if not parent_task:
                return jsonify({"error": "Parent task not found"}), 404
        
        # Try to create task on CalDAV server
        new_uid = str(uuid.uuid4())
        is_synced = True
        operation = None
        
        if client:
            result, error = handle_caldav_error(
                client.create_task,
                summary, calendar_url, parent_uid, description, due, priority
            )
            
            if result:
                new_uid = result
                log_sync_operation(calendar_url, 'create', new_uid, 'success')
            else:
                is_synced = False
                operation = "create"
                log_sync_operation(calendar_url, 'create', new_uid, 'error', error_details=error)
        else:
            is_synced = False
            operation = "create"
        
        # Create local task
        new_task = Task(
            uid=new_uid,
            summary=summary,
            completed=False,
            parent_uid=parent_uid,
            description=description,
            due=due,
            priority=priority,
            tags=json.dumps(tags) if tags else None,
            estimated_duration=estimated_duration,
            calendar_url=calendar_url,
            is_synced=is_synced,
            operation=operation
        )
        
        db.session.add(new_task)
        db.session.commit()
        
        return jsonify({
            "status": "created",
            "uid": new_uid,
            "is_synced": is_synced,
            "task": new_task.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.exception("Failed to create task")
        return jsonify({"error": str(e)}), 500

@app.route('/tasks/<uid>', methods=['GET'])
def get_task(uid):
    """Get a specific task by UID"""
    calendar_url = request.args.get("calendar_url")
    if not calendar_url:
        return jsonify({'error': 'Missing calendar_url parameter'}), 400
    
    task = Task.query.filter_by(uid=uid, calendar_url=calendar_url).first()
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    # Include subtasks in response
    task_dict = task.to_dict()
    task_dict['subtasks'] = [subtask.to_dict() for subtask in task.subtasks]
    
    return jsonify(task_dict)

@app.route('/tasks/<uid>', methods=['PATCH'])
@validate_json_request()
def update_task(data, uid):
    """Update an existing task"""
    calendar_url = data.get("calendar_url")
    if not calendar_url:
        return jsonify({'error': 'Missing calendar_url'}), 400
    
    task = Task.query.filter_by(uid=uid, calendar_url=calendar_url).first()
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    try:
        # Track what changed
        changes = {}
        
        # Update task fields
        if 'completed' in data:
            new_completed = bool(data['completed'])
            if new_completed != task.completed:
                if new_completed:
                    task.mark_completed()
                else:
                    task.mark_incomplete()
                changes['completed'] = new_completed
        
        if 'summary' in data and data['summary'].strip():
            new_summary = data['summary'].strip()
            if new_summary != task.summary:
                task.summary = new_summary
                changes['summary'] = new_summary
        
        if 'description' in data:
            if data['description'] != task.description:
                task.description = data['description']
                changes['description'] = data['description']
        
        if 'priority' in data:
            new_priority = data['priority']
            if new_priority is not None:
                new_priority = int(new_priority)
                if new_priority < 0 or new_priority > 9:
                    return jsonify({"error": "Priority must be between 0 and 9"}), 400
            if new_priority != task.priority:
                task.priority = new_priority
                changes['priority'] = new_priority
        
        if 'due' in data:
            new_due = None
            if data['due']:
                try:
                    new_due = parse(data['due'])
                except Exception:
                    return jsonify({"error": "Invalid 'due' datetime format"}), 400
            if new_due != task.due:
                task.due = new_due
                changes['due'] = new_due
        
        if 'parent_uid' in data:
            new_parent = data['parent_uid']
            if new_parent != task.parent_uid:
                # Validate parent exists
                if new_parent:
                    parent_task = Task.query.filter_by(uid=new_parent, calendar_url=calendar_url).first()
                    if not parent_task:
                        return jsonify({"error": "Parent task not found"}), 404
                task.parent_uid = new_parent
                changes['parent_uid'] = new_parent
        
        if 'tags' in data:
            new_tags = json.dumps(data['tags']) if data['tags'] else None
            if new_tags != task.tags:
                task.tags = new_tags
                changes['tags'] = data['tags']
        
        if 'estimated_duration' in data:
            if data['estimated_duration'] != task.estimated_duration:
                task.estimated_duration = data['estimated_duration']
                changes['estimated_duration'] = data['estimated_duration']
        
        if 'actual_duration' in data:
            if data['actual_duration'] != task.actual_duration:
                task.actual_duration = data['actual_duration']
                changes['actual_duration'] = data['actual_duration']
        
        # Only update if there are actual changes
        if not changes:
            return jsonify({'message': 'No changes detected'})
        
        # Try to sync with CalDAV
        if client:
            todo = client.get_task_by_uid(uid, calendar_url)
            result, error = handle_caldav_error(
                client.update_task,
                todo=todo,
                completed=task.completed,
                summary=task.summary,
                description=task.description,
                due=task.due,
                priority=task.priority,
                parent_uid=task.parent_uid
            )

            
            if result:
                task.mark_synced()
                log_sync_operation(calendar_url, 'update', uid, 'success')
            else:
                task.mark_for_sync('update')
                log_sync_operation(calendar_url, 'update', uid, 'error', error_details=error)
        else:
            task.mark_for_sync('update')
        
        db.session.commit()
        
        return jsonify({
            'message': 'Task updated',
            'changes': changes,
            'is_synced': task.is_synced,
            'task': task.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        logger.exception("Failed to update task")
        return jsonify({"error": str(e)}), 500

@app.route('/tasks/<uid>', methods=['DELETE'])
@require_calendar_url
def delete_task(calendar_url, uid):
    """Delete a task"""
    task = Task.query.filter_by(uid=uid, calendar_url=calendar_url).first()
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    try:
        # Try to delete from CalDAV server
        if client:
            result, error = handle_caldav_error(client.delete_task, uid, calendar_url)
            
            if result is not None:  # Success
                db.session.delete(task)
                log_sync_operation(calendar_url, 'delete', uid, 'success')
            else:
                task.mark_for_sync('delete')
                log_sync_operation(calendar_url, 'delete', uid, 'error', error_details=error)
        else:
            task.mark_for_sync('delete')
        
        db.session.commit()
        return jsonify({'message': 'Task deleted', 'is_synced': task.is_synced if task.operation == 'delete' else True})
        
    except Exception as e:
        db.session.rollback()
        logger.exception("Failed to delete task")
        return jsonify({"error": str(e)}), 500

@app.route('/calendars', methods=['GET'])
def list_calendars():
    """List available calendars"""
    if not client:
        # Return cached calendars if CalDAV is unavailable
        cached_calendars = Calendar.query.filter_by(is_active=True).all()
        return jsonify([cal.to_dict() for cal in cached_calendars])
    
    try:
        calendars = client.list_calendars()
        
        # Update cache
        for cal_data in calendars:
            calendar = Calendar.query.filter_by(url=cal_data['url']).first()
            if not calendar:
                calendar = Calendar(url=cal_data['url'])
                db.session.add(calendar)
            
            calendar.name = cal_data['name']
            calendar.display_name = cal_data.get('display_name', cal_data['name'])
            calendar.description = cal_data.get('description')
            calendar.color = cal_data.get('color')
            calendar.is_active = True
            calendar.last_sync = datetime.now(timezone.utc)
        
        db.session.commit()
        return jsonify(calendars)
        
    except Exception as e:
        logger.exception("Failed to list calendars")
        return jsonify({'error': 'Failed to list calendars', 'details': str(e)}), 500

@app.route('/push_pending', methods=['POST'])
@require_calendar_url
def push_pending(calendar_url):
    """Push pending sync operations to CalDAV server"""
    if not client:
        return jsonify({'error': 'CalDAV client not available'}), 503
    
    unsynced_tasks = Task.query.filter_by(
        is_synced=False, 
        calendar_url=calendar_url
    ).order_by(Task.updated.asc()).all()
    
    if not unsynced_tasks:
        return jsonify({'status': 'no_pending_tasks', 'pushed': 0, 'errors': []})
    
    errors = []
    pushed_count = 0
    
    for task in unsynced_tasks:
        # try:
        if task.operation == "create":
            result = client.create_task(
                task.summary, calendar_url, task.parent_uid,
                task.description, task.due, task.priority
            )
            if result:
                task.uid = result  # Update with server-generated UID
                        
        elif task.operation == "update":
            todo = client.get_task_by_uid(task.uid, calendar_url)
            if not todo:
                raise Exception(f"Task {task.uid} not found in remote calendar")

            client.update_task(
                todo=todo,
                summary=task.summary,
                description=task.description,
                due=task.due,
                priority=task.priority,
                parent_uid=task.parent_uid,
                completed=task.completed
            )


            
        elif task.operation == "delete":
            client.delete_task(task.uid, calendar_url)
            db.session.delete(task)
            pushed_count += 1
            continue
        
        task.mark_synced()
        pushed_count += 1
        log_sync_operation(calendar_url, operation=task.operation or 'unknown', task_uid=task.uid, status='success')
        
        # except Exception as e:
        #     task.increment_sync_attempts()
        #     error_msg = str(e)
        #     errors.append({'uid': task.uid, 'operation': task.operation, 'error': error_msg})
        #     log_sync_operation(calendar_url, task.operation, task.uid, 'error', error_details=error_msg)
        #     logger.error(f"Failed to push task {task.uid}: {e}")
    
    db.session.commit()
    
    return jsonify({
        'status': 'completed',
        'pushed': pushed_count,
        'errors': errors,
        'remaining_unsynced': len(unsynced_tasks) - pushed_count
    })

@app.route('/tasks/bulk', methods=['POST'])
@validate_json_request(['operation'])
def bulk_task_operations(data):
    """Perform bulk operations on tasks"""
    calendar_url = data.get("calendar_url")
    if not calendar_url:
        return jsonify({'error': 'Missing calendar_url'}), 400
    
    operation = data.get('operation')
    task_uids = data.get('task_uids', [])
    
    if not task_uids:
        return jsonify({'error': 'No task UIDs provided'}), 400
    
    valid_operations = ['complete', 'incomplete', 'delete', 'set_priority']
    if operation not in valid_operations:
        return jsonify({'error': f'Invalid operation. Must be one of: {valid_operations}'}), 400
    
    try:
        tasks = Task.query.filter(
            Task.uid.in_(task_uids),
            Task.calendar_url == calendar_url
        ).all()
        
        if not tasks:
            return jsonify({'error': 'No matching tasks found'}), 404
        
        updated_count = 0
        errors = []
        
        for task in tasks:
            try:
                if operation == 'complete':
                    if not task.completed:
                        task.mark_completed()
                        updated_count += 1
                elif operation == 'incomplete':
                    if task.completed:
                        task.mark_incomplete()
                        updated_count += 1
                elif operation == 'delete':
                    if client:
                        result, error = handle_caldav_error(client.delete_task, task.uid, calendar_url)
                        if result is not None:
                            db.session.delete(task)
                        else:
                            task.mark_for_sync('delete')
                    else:
                        task.mark_for_sync('delete')
                    updated_count += 1
                elif operation == 'set_priority':
                    priority = data.get('priority')
                    if priority is not None:
                        task.priority = int(priority)
                        task.mark_for_sync('update')
                        updated_count += 1
                
                # Mark for sync if needed
                if operation in ['complete', 'incomplete'] and client:
                    result, error = handle_caldav_error(
                        client.update_task,
                        task.uid, calendar_url,
                        completed=task.completed,
                        summary=task.summary,
                        description=task.description,
                        due=task.due,
                        priority=task.priority,
                        parent_uid=task.parent_uid
                    )
                    
                    if result:
                        task.mark_synced()
                    else:
                        task.mark_for_sync('update')
                        
            except Exception as e:
                errors.append({'uid': task.uid, 'error': str(e)})
        
        db.session.commit()
        
        return jsonify({
            'status': 'completed',
            'operation': operation,
            'updated_count': updated_count,
            'total_requested': len(task_uids),
            'errors': errors
        })
        
    except Exception as e:
        db.session.rollback()
        logger.exception("Bulk operation failed")
        return jsonify({'error': str(e)}), 500

@app.route('/tasks/search', methods=['GET'])
@require_calendar_url
def search_tasks(calendar_url):
    """Search tasks by various criteria"""
    query = Task.query.filter_by(calendar_url=calendar_url)
    
    # Text search in summary and description
    search_text = request.args.get('q', '').strip()
    if search_text:
        search_pattern = f'%{search_text}%'
        query = query.filter(
            db.or_(
                Task.summary.ilike(search_pattern),
                Task.description.ilike(search_pattern)
            )
        )
    
    # Filter by tags
    tags = request.args.get('tags', '').strip()
    if tags:
        tag_list = [tag.strip() for tag in tags.split(',')]
        for tag in tag_list:
            query = query.filter(Task.tags.ilike(f'%{tag}%'))
    
    # Filter by priority range
    min_priority = request.args.get('min_priority')
    max_priority = request.args.get('max_priority')
    if min_priority:
        query = query.filter(Task.priority >= int(min_priority))
    if max_priority:
        query = query.filter(Task.priority <= int(max_priority))
    
    # Filter by date range
    due_after = request.args.get('due_after')
    due_before = request.args.get('due_before')
    if due_after:
        query = query.filter(Task.due >= parse(due_after))
    if due_before:
        query = query.filter(Task.due <= parse(due_before))
    
    # Sort options
    sort_by = request.args.get('sort_by', 'updated')
    sort_order = request.args.get('sort_order', 'desc')
    
    if sort_by == 'due':
        order_field = Task.due
    elif sort_by == 'priority':
        order_field = Task.priority
    elif sort_by == 'created':
        order_field = Task.created
    else:
        order_field = Task.updated
    
    if sort_order == 'asc':
        query = query.order_by(order_field.asc())
    else:
        query = query.order_by(order_field.desc())
    
    # Pagination
    page = int(request.args.get('page', 1))
    per_page = min(int(request.args.get('per_page', 50)), 200)
    
    paginated_results = query.paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )
    
    return jsonify({
        'tasks': [task.to_dict() for task in paginated_results.items],
        'search_params': {
            'query': search_text,
            'tags': tags,
            'min_priority': min_priority,
            'max_priority': max_priority,
            'due_after': due_after,
            'due_before': due_before,
            'sort_by': sort_by,
            'sort_order': sort_order
        },
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': paginated_results.total,
            'pages': paginated_results.pages,
            'has_next': paginated_results.has_next,
            'has_prev': paginated_results.has_prev
        }
    })

@app.route('/stats', methods=['GET'])
@require_calendar_url
def get_task_stats(calendar_url):
    """Get task statistics and analytics"""
    try:
        # Basic counts
        total_tasks = Task.query.filter_by(calendar_url=calendar_url).count()
        completed_tasks = Task.query.filter_by(calendar_url=calendar_url, completed=True).count()
        pending_tasks = total_tasks - completed_tasks
        
        # Overdue tasks - handle timezone-naive dates properly
        overdue_count = 0
        now_utc = datetime.now(timezone.utc)
        tasks_with_due_dates = Task.query.filter(
            Task.calendar_url == calendar_url,
            Task.due.isnot(None),
            Task.completed == False
        ).all()
        
        for task in tasks_with_due_dates:
            due_date = task.due
            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)
            if due_date < now_utc:
                overdue_count += 1
        
        # Tasks by priority
        priority_stats = {}
        for i in range(1, 10):
            count = Task.query.filter_by(
                calendar_url=calendar_url,
                priority=i,
                completed=False
            ).count()
            if count > 0:
                priority_stats[str(i)] = count
        
        # Sync status
        unsynced_tasks = Task.query.filter_by(
            calendar_url=calendar_url,
            is_synced=False
        ).count()
        
        # Recent activity (last 7 days)
        from datetime import timedelta
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        
        recent_completed = Task.query.filter(
            Task.calendar_url == calendar_url,
            Task.completed == True,
            Task.completed_at >= week_ago
        ).count()
        
        recent_created = Task.query.filter(
            Task.calendar_url == calendar_url,
            Task.created >= week_ago
        ).count()
        
        # Task hierarchy stats
        parent_tasks = Task.query.filter_by(
            calendar_url=calendar_url,
            parent_uid=None
        ).count()
        
        subtasks = Task.query.filter(
            Task.calendar_url == calendar_url,
            Task.parent_uid.isnot(None)
        ).count()
        
        return jsonify({
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'pending_tasks': pending_tasks,
            'overdue_tasks': overdue_count,
            'completion_rate': round((completed_tasks / total_tasks * 100) if total_tasks > 0 else 0, 1),
            'priority_distribution': priority_stats,
            'unsynced_tasks': unsynced_tasks,
            'recent_activity': {
                'completed_last_week': recent_completed,
                'created_last_week': recent_created
            },
            'hierarchy': {
                'parent_tasks': parent_tasks,
                'subtasks': subtasks
            },
            'generated_at': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logger.exception("Failed to generate stats")
        return jsonify({'error': str(e)}), 500

@app.route('/sync_logs', methods=['GET'])
@require_calendar_url
def get_sync_logs(calendar_url):
    """Get synchronization logs"""
    page = int(request.args.get('page', 1))
    per_page = min(int(request.args.get('per_page', 50)), 200)
    status_filter = request.args.get('status')  # success, error, warning
    
    query = SyncLog.query.filter_by(calendar_url=calendar_url)
    
    if status_filter:
        query = query.filter_by(status=status_filter)
    
    query = query.order_by(SyncLog.timestamp.desc())
    
    paginated_logs = query.paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )
    
    logs = []
    for log in paginated_logs.items:
        logs.append({
            'id': log.id,
            'operation': log.operation,
            'task_uid': log.task_uid,
            'status': log.status,
            'message': log.message,
            'error_details': log.error_details,
            'timestamp': log.timestamp.isoformat()
        })
    
    return jsonify({
        'logs': logs,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': paginated_logs.total,
            'pages': paginated_logs.pages,
            'has_next': paginated_logs.has_next,
            'has_prev': paginated_logs.has_prev
        }
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
