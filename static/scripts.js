let currentCalendar = null;
let draggedUID = null;
let currentView = 'list'; // 'list' or 'board'
let currentPriorityFilter = ''; // empty string means show all
let allActiveTasks = []; // Store all active tasks for filtering

// Toast notification system
function showToast(message, type = 'info', duration = 3000) {
  // Create toast container if it doesn't exist
  let toastContainer = document.getElementById('toast-container');
  if (!toastContainer) {
    toastContainer = document.createElement('div');
    toastContainer.id = 'toast-container';
    document.body.appendChild(toastContainer);
  }

  // Create toast element
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  // Add icon based on type
  const icons = {
    success: '✅',
    error: '❌',
    warning: '⚠️',
    info: 'ℹ️'
  };

  toast.innerHTML = `
    <div class="toast-content">
      <span class="toast-icon">${icons[type] || icons.info}</span>
      <span>${message}</span>
    </div>
  `;

  // Add progress bar for duration
  if (duration > 0) {
    const progressBar = document.createElement('div');
    progressBar.className = 'toast-progress';
    progressBar.style.animation = `toast-progress ${duration}ms linear forwards`;
    toast.appendChild(progressBar);
  }

  // Click to dismiss
  toast.addEventListener('click', () => {
    removeToast(toast);
  });

  // Add to container
  toastContainer.appendChild(toast);

  // Animate in
  requestAnimationFrame(() => {
    toast.classList.add('show');
  });

  // Auto remove after duration
  if (duration > 0) {
    setTimeout(() => {
      if (toast.parentNode) {
        removeToast(toast);
      }
    }, duration);
  }

  return toast;
}

function removeToast(toast) {
  toast.classList.remove('show');
  setTimeout(() => {
    if (toast.parentNode) {
      toast.parentNode.removeChild(toast);
    }
  }, 300);
}

// Replace the fetchTasks function in scripts.js
async function fetchTasks() {
  if (!currentCalendar) {
    document.getElementById('taskList').textContent = 'Please select a calendar first';
    document.getElementById('completedTasks').innerHTML = '';
    return;
  }

  // Show loading state
  showTaskListLoading();

  const url = `/tasks?calendar_url=${encodeURIComponent(currentCalendar)}`;
  console.log("Fetching tasks from:", url);
  
  try {
    const res = await fetch(url);
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}`);
    }
    
    const data = await res.json();
    console.log("Received data:", data);
    
    // Handle both paginated and non-paginated responses
    let tasks;
    if (data.tasks && Array.isArray(data.tasks)) {
      tasks = data.tasks;
    } else if (Array.isArray(data)) {
      tasks = data;
    } else {
      console.error("Unexpected data format:", data);
      tasks = [];
    }
    
    console.log("Parsed tasks:", tasks);
    
    const active = tasks.filter(t => !t.completed);
    const completed = tasks.filter(t => t.completed);
    
    console.log("Active tasks:", active.length, "Completed tasks:", completed.length);
    
    // Hide loading and show content
    hideTaskListLoading();
    
    document.getElementById('taskList').innerHTML = '';
    document.getElementById('completedTasks').innerHTML = '';
    renderTasks(active, document.getElementById('taskList'));
    renderTasks(completed, document.getElementById('completedTasks'));
    
    // Show empty state if no active tasks
    if (active.length === 0) {
      showTaskListEmpty();
    }
    
  } catch (error) {
    console.error('Failed to fetch tasks:', error);
    hideTaskListLoading();
    showTaskListError(error.message);
    showToast('Failed to load tasks: ' + error.message, 'error');
  }
}

// Add these new helper functions to scripts.js
function showTaskListLoading() {
  document.getElementById('taskListLoading').classList.remove('hidden');
  document.getElementById('taskListEmpty').classList.add('hidden');
  document.getElementById('taskListError').classList.add('hidden');
  document.getElementById('taskList').style.display = 'none';
}

function hideTaskListLoading() {
  document.getElementById('taskListLoading').classList.add('hidden');
  document.getElementById('taskList').style.display = '';
}

function showTaskListEmpty() {
  document.getElementById('taskListEmpty').classList.remove('hidden');
  document.getElementById('taskListError').classList.add('hidden');
}

function showTaskListError(message) {
  document.getElementById('taskListError').classList.remove('hidden');
  document.getElementById('taskListEmpty').classList.add('hidden');
  document.getElementById('taskListErrorMessage').textContent = message;
}

async function updateTask(uid, data) {
  if (!currentCalendar) {
    showToast('No calendar selected', 'warning');
    return;
  }
  
  data.calendar_url = currentCalendar;
  
  try {
    const res = await fetch('/tasks/' + uid, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}`);
    }
    
    const result = await res.json();
    showToast(result.message || 'Task updated successfully', 'success', 2000);
    fetchTasks();
    
  } catch (error) {
    console.error('Failed to update task:', error);
    showToast('Failed to update task: ' + error.message, 'error');
  }
}

async function deleteTask(uid) {
  if (!currentCalendar) {
    showToast('No calendar selected', 'warning');
    return;
  }
  
  try {
    const res = await fetch(`/tasks/${uid}?calendar_url=${encodeURIComponent(currentCalendar)}`, {
      method: 'DELETE'
    });
    
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}`);
    }
    
    const result = await res.json();
    showToast(result.message || 'Task deleted successfully', 'success', 2000);
    fetchTasks();
    
  } catch (error) {
    console.error('Failed to delete task:', error);
    showToast('Failed to delete task: ' + error.message, 'error');
  }
}

async function addSubtask(parentUid, subtaskSummary) {
  if (!subtaskSummary.trim() || !currentCalendar) return;
  
  try {
    const res = await fetch('/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        summary: subtaskSummary.trim(), // Use 'summary' not 'title'
        parent_uid: parentUid,
        calendar_url: currentCalendar
      })
    });
    
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}`);
    }
    
    const result = await res.json();
    showToast(result.status === 'created' ? 'Subtask created successfully' : 'Subtask created', 'success', 2000);
    fetchTasks();
    
  } catch (error) {
    console.error('Failed to create subtask:', error);
    showToast('Failed to create subtask: ' + error.message, 'error');
  }
}

function createTaskItem(task, allTasks) {
  const li = document.createElement('li');
  li.setAttribute('draggable', true);
  li.dataset.uid = task.uid;
  if (task.completed) li.classList.add('completed');

  // Drag events
  li.addEventListener('dragstart', e => {
    draggedUID = task.uid;
    e.dataTransfer.effectAllowed = "move";
  });
  li.addEventListener('dragover', e => {
    e.preventDefault();
    li.classList.add('drag-over');
  });
  li.addEventListener('dragleave', () => {
    li.classList.remove('drag-over');
  });
  li.addEventListener('drop', async e => {
    e.preventDefault();
    li.classList.remove('drag-over');
    if (draggedUID !== task.uid) {
      await updateTask(draggedUID, { parent_uid: task.uid });
    }
  });

  // Main container
  const mainLine = document.createElement('div');
  mainLine.className = 'task-main-line';

  // Left side container
  const leftSide = document.createElement('div');
  leftSide.className = 'task-left-side';

  // Top row: checkbox + title
  const topRow = document.createElement('div');
  topRow.className = 'task-top-row';

  // Checkbox
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = task.completed;
  checkbox.title = 'Mark task complete';
  checkbox.onclick = () => updateTask(task.uid, { completed: checkbox.checked });
  topRow.appendChild(checkbox);

  // Title editable span
  const titleSpan = document.createElement('span');
  titleSpan.className = 'task-title';
  titleSpan.textContent = task.summary || '';
  titleSpan.title = 'Click to edit task title';
  titleSpan.contentEditable = false;

  titleSpan.onclick = e => {
    e.stopPropagation();
    if (!titleSpan.isContentEditable) {
      titleSpan.contentEditable = true;
      titleSpan.focus();
      document.execCommand('selectAll', false, null);
    }
  };
  titleSpan.onblur = () => {
    const val = titleSpan.textContent.trim();
    if (val && val !== task.summary) updateTask(task.uid, { summary: val });
    titleSpan.contentEditable = false;
  };
  titleSpan.onkeydown = e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      titleSpan.blur();
    }
  };

  topRow.appendChild(titleSpan);

  // Unsynced badge
  if (!task.is_synced) {
    const badge = document.createElement('span');
    badge.textContent = '⛔ Offline';
    badge.title = 'This task is not yet synced with CalDAV';
    badge.className = 'unsynced-badge';
    topRow.appendChild(badge);
  }

  leftSide.appendChild(topRow);

  // Description below title
  if (task.description && task.description.trim()) {
    const descSpan = document.createElement('div');
    descSpan.className = 'task-desc';
    descSpan.textContent = task.description;
    leftSide.appendChild(descSpan);
  }

  mainLine.appendChild(leftSide);

  // Right side container
  const rightSide = document.createElement('div');
  rightSide.className = 'task-right-side';

  // Priority flag - match API priority values
  const priorityEmojis = {
    1: '🔴',    // High
    5: '🟠',    // Medium  
    9: '🟢',    // Low
    0: '⚪', // None
    undefined: '⚪'
  };

  const flagBtn = document.createElement('span');
  flagBtn.className = 'priority-flag';
  flagBtn.title = 'Click to change priority';
  flagBtn.textContent = priorityEmojis[task.priority];

  const priorityDropdown = document.createElement('div');
  priorityDropdown.className = 'priority-dropdown';

  const levelMap = {
    'High': { val: 1, emoji: '🔴' },    // Use numbers, not strings
    'Medium': { val: 5, emoji: '🟠' },
    'Low': { val: 9, emoji: '🟢' },
    'None': { val: 0, emoji: '⚪' }
  };

  Object.entries(levelMap).forEach(([label, { val, emoji }]) => {
    const opt = document.createElement('div');
    opt.textContent = `${emoji} ${label}`;
    opt.className = 'priority-option';
    opt.onclick = (e) => {
      e.stopPropagation();
      updateTask(task.uid, { priority: val });
      priorityDropdown.classList.remove('show');
    };
    priorityDropdown.appendChild(opt);
  });

  // Toggle dropdown
  flagBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    
    // Hide all other priority dropdowns
    document.querySelectorAll('.priority-dropdown').forEach(dropdown => {
      if (dropdown !== priorityDropdown) {
        dropdown.classList.remove('show');
      }
    });
    
    priorityDropdown.classList.toggle('show');
    
    if (priorityDropdown.classList.contains('show')) {
      // Position the dropdown
      const rect = flagBtn.getBoundingClientRect();
      priorityDropdown.style.left = `${rect.left}px`;
      priorityDropdown.style.top = `${rect.bottom + 2}px`;
    }
  });

  const flagWrapper = document.createElement('div');
  flagWrapper.className = 'priority-flag-wrapper';
  flagWrapper.appendChild(flagBtn);
  
  // Append dropdown to body for better positioning
  document.body.appendChild(priorityDropdown);
  
  // Store reference for cleanup
  flagBtn._dropdown = priorityDropdown;

  // Check if task has a valid priority - use numbers
  const hasValidPriority = task.priority === 1 || task.priority === 5 || task.priority === 9;
  
  if (hasValidPriority) {
    rightSide.appendChild(flagWrapper);
  } else {
    flagWrapper.classList.add('hidden-by-default');
    rightSide.appendChild(flagWrapper);
  }

  // Due date display or schedule button
  function formatDueDate(iso) {
    if (!iso) return '';
    const dt = new Date(iso);
    if (isNaN(dt)) return '';
    const options = {
      hour: 'numeric', minute: 'numeric',
      weekday: 'long',
      year: 'numeric', month: 'long', day: 'numeric'
    };
    const day = dt.getDate();
    let suffix = 'th';
    if (day % 10 === 1 && day !== 11) suffix = 'st';
    else if (day % 10 === 2 && day !== 12) suffix = 'nd';
    else if (day % 10 === 3 && day !== 13) suffix = 'rd';

    const dateStr = dt.toLocaleString(undefined, options);
    return dateStr.replace(day.toString(), day + suffix);
  }

  // Create a container for due date or schedule icon
  const dueContainer = document.createElement('div');
  dueContainer.className = 'task-due';

  function openDueDatePicker(task, dueContainer) {
    const input = document.createElement('input');
    input.type = 'text';
    input.style.position = 'absolute';
    input.style.opacity = '0';
    input.style.pointerEvents = 'none';

    document.body.appendChild(input);

    const fp = flatpickr(input, {
      enableTime: true,
      dateFormat: "Y-m-d\\TH:i",
      defaultDate: task.due || null,
      onClose: function(selectedDates) {
        if (selectedDates.length > 0) {
          const iso = new Date(selectedDates[0].getTime() - selectedDates[0].getTimezoneOffset() * 60000).toISOString();
          updateTask(task.uid, { due: iso });
        }
        input.remove();
      }
    });

    const rect = dueContainer.getBoundingClientRect();
    fp.open();
    fp.calendarContainer.style.position = 'absolute';
    fp.calendarContainer.style.left = `${rect.left}px`;
    fp.calendarContainer.style.top = `${rect.bottom + window.scrollY}px`;
  }

  if (task.due) {
    dueContainer.textContent = formatDueDate(task.due);
    dueContainer.title = 'Click to edit due date/time';
    dueContainer.onclick = e => {
      e.stopPropagation();
      openDueDatePicker(task, dueContainer);
    };
    rightSide.appendChild(dueContainer);
  } else {
    const scheduleBtn = document.createElement('button');
    scheduleBtn.className = 'schedule-btn';
    scheduleBtn.title = 'Set due date/time';
    scheduleBtn.textContent = '⏰';

    scheduleBtn.onclick = e => {
      e.stopPropagation();
      openDueDatePicker(task, dueContainer);
    };
    dueContainer.appendChild(scheduleBtn);
    dueContainer.classList.add('hidden-by-default');
    rightSide.appendChild(dueContainer);
  }

  // Pencil icon for editing description
  const pencilBtn = document.createElement('button');
  pencilBtn.className = 'edit-desc-btn';
  pencilBtn.title = 'Edit description';
  pencilBtn.textContent = '✏️';

  pencilBtn.onclick = e => {
    e.stopPropagation();
    const newDesc = prompt('Edit task description:', task.description || '');
    if (newDesc !== null && newDesc !== task.description) {
      updateTask(task.uid, { description: newDesc });
    }
  };

  if (task.description && task.description.trim()) {
    rightSide.appendChild(pencilBtn);
  } else {
    pencilBtn.classList.add('hidden-by-default');
    rightSide.appendChild(pencilBtn);
  }

  // Delete button (×)
  const delBtn = document.createElement('span');
  delBtn.textContent = '×';
  delBtn.className = 'delete-btn';
  delBtn.title = 'Delete task';
  delBtn.onclick = () => {
    // Show confirmation toast instead of alert
    const confirmToast = showToast('Click again to confirm deletion', 'warning', 0);
    confirmToast.style.cursor = 'pointer';
    confirmToast.onclick = () => {
      removeToast(confirmToast);
      deleteTask(task.uid);
    };
    
    // Auto-dismiss confirmation after 3 seconds
    setTimeout(() => {
      if (confirmToast.parentNode) {
        removeToast(confirmToast);
      }
    }, 3000);
  };
  rightSide.appendChild(delBtn);

  mainLine.appendChild(rightSide);
  li.appendChild(mainLine);

  // Subtasks container
  const subtasksContainer = document.createElement('ul');
  subtasksContainer.className = 'subtasks';
  allTasks.filter(t => t.parent_uid === task.uid).forEach(sub => {
    subtasksContainer.appendChild(createTaskItem(sub, allTasks));
  });
  li.appendChild(subtasksContainer);

  // Subtask input
  const subtaskInputDiv = document.createElement('div');
  subtaskInputDiv.className = 'subtask-input-container';
  
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'Add subtask...';
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (input.value.trim()) {
        addSubtask(task.uid, input.value);
        input.value = '';
      }
    }
  });
  
  const addBtn = document.createElement('button');
  addBtn.textContent = 'Add';
  addBtn.onclick = () => {
    if (input.value.trim()) {
      addSubtask(task.uid, input.value);
      input.value = '';
    }
  };
  
  subtaskInputDiv.appendChild(input);
  subtaskInputDiv.appendChild(addBtn);
  li.appendChild(subtaskInputDiv);

  // Task click handler
  li.addEventListener('click', (e) => {
    // Don't handle if clicking on interactive elements
    if (
      e.target.tagName === 'INPUT' ||
      e.target.tagName === 'BUTTON' ||
      e.target.isContentEditable ||
      e.target.classList.contains('priority-flag') ||
      e.target.classList.contains('delete-btn')
    ) {
      return;
    }

    e.stopPropagation();

    // Show ALL hidden elements in this task's rightSide container
    rightSide.querySelectorAll('.hidden-by-default').forEach(el => {
      el.classList.add('visible');
    });

    // Hide other tasks' subtask inputs
    document.querySelectorAll('.subtask-input-container').forEach(div => {
      if (div !== subtaskInputDiv) {
        div.classList.remove('show');
      }
    });

    // Toggle this subtask input
    subtaskInputDiv.classList.toggle('show');
  });

  return li;
}

// Global click handler
document.addEventListener('click', (e) => {
  // Hide priority dropdowns when clicking outside
  if (!e.target.classList.contains('priority-flag') && 
      !e.target.classList.contains('priority-option') &&
      !e.target.closest('.priority-dropdown')) {
    document.querySelectorAll('.priority-dropdown').forEach(dropdown => {
      dropdown.classList.remove('show');
    });
  }

  // Only hide visible elements if clicking completely outside any task
  if (!e.target.closest('li[data-uid]')) {
    document.querySelectorAll('.hidden-by-default.visible').forEach(el => {
      el.classList.remove('visible');
    });
    
    document.querySelectorAll('.subtask-input-container').forEach(div => {
      div.classList.remove('show');
    });
  }
});

function renderTasks(tasks, container) {
  console.log("Rendering tasks to container:", container.id, "Tasks:", tasks);
  
  container.innerHTML = '';
  
  // Store active tasks for filtering (only for main task list, not completed)
  if (container.id === 'taskList') {
    allActiveTasks = tasks;
    tasks = filterTasksByPriority(tasks);
  }
  
  const topLevelTasks = tasks.filter(t => !t.parent_uid);
  
  console.log("Top level tasks after filtering:", topLevelTasks);
  
  if (topLevelTasks.length === 0) {
    if (container.id === 'taskList' && currentPriorityFilter) {
      container.innerHTML = '<div class="empty-filter-state">No tasks match the selected priority filter</div>';
    } else {
      container.textContent = '(No tasks)';
    }
    return;
  }
  
  if (currentView === 'board' && container.id === 'taskList') {
    renderBoardView(topLevelTasks, tasks, container);
  } else {
    renderListView(topLevelTasks, tasks, container);
  }
}

// Add new function for list view rendering
function renderListView(topLevelTasks, allTasks, container) {
  const ul = document.createElement('ul');
  topLevelTasks.forEach(task => {
    console.log("Creating task item for:", task.summary);
    ul.appendChild(createTaskItem(task, allTasks));
  });
  container.appendChild(ul);
}

// Add new function for board view rendering
function renderBoardView(topLevelTasks, allTasks, container) {
  container.className = 'task-board';
  
  const columns = [
    { title: '🔴 High Priority', priority: 1, tasks: [] },
    { title: '🟠 Medium Priority', priority: 5, tasks: [] },
    { title: '🟢 Low Priority', priority: 9, tasks: [] },
    { title: '⚪ No Priority', priority: 0, tasks: [] }
  ];
  
  // Sort tasks into columns
  topLevelTasks.forEach(task => {
    const column = columns.find(col => col.priority === task.priority) || columns[3];
    column.tasks.push(task);
  });
  
  columns.forEach(column => {
    const columnDiv = document.createElement('div');
    columnDiv.className = 'board-column';
    
    const header = document.createElement('div');
    header.className = 'board-column-header';
    header.innerHTML = `
      <h3>${column.title}</h3>
      <span class="task-count">${column.tasks.length}</span>
    `;
    columnDiv.appendChild(header);
    
    const taskList = document.createElement('ul');
    taskList.className = 'board-column-tasks';
    
    column.tasks.forEach(task => {
      const taskItem = createTaskItem(task, allTasks);
      taskItem.classList.add('board-task-item');
      taskList.appendChild(taskItem);
    });
    
    columnDiv.appendChild(taskList);
    container.appendChild(columnDiv);
  });
}

// Add function to filter tasks by priority
function filterTasksByPriority(tasks) {
  if (!currentPriorityFilter) {
    return tasks;
  }
  
  const filterValue = currentPriorityFilter === 'null' ? null : parseInt(currentPriorityFilter);
  return tasks.filter(task => task.priority === filterValue);
}

async function fetchCalendars() {
  try {
    const res = await fetch('/calendars');
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}`);
    }
    
    const calendars = await res.json();
    const list = document.getElementById('calendarList');
    list.innerHTML = '';
    
    calendars.forEach(cal => {
      const li = document.createElement('li');
      li.textContent = cal.display_name || cal.name;
      li.dataset.url = cal.url;
      li.onclick = () => loadTasksForCalendar(cal.url);
      list.appendChild(li);
    });
    
    if (calendars.length > 0) {
      loadTasksForCalendar(calendars[0].url);
    } else {
      showToast('No calendars found', 'warning');
    }
    
  } catch (error) {
    console.error("Failed to fetch calendars:", error);
    showToast('Failed to fetch calendars: ' + error.message, 'error');
  }
}

function loadTasksForCalendar(calendarUrl) {
  console.log("Switching to calendar:", calendarUrl);
  currentCalendar = calendarUrl;
  document.querySelectorAll('#calendarList li').forEach(li => {
    li.classList.toggle('active', li.dataset.url === calendarUrl);
  });
  
  // Show loading immediately
  showTaskListLoading();
  document.getElementById('completedTasks').innerHTML = '';
  
  fetchTasks();
}

document.getElementById('taskForm').addEventListener('submit', async (e) => {
  e.preventDefault();

  if (!currentCalendar) {
    showToast('Please select a calendar first', 'warning');
    return;
  }

  const summary = document.getElementById('taskTitle').value.trim(); // Changed from title to summary
  const description = document.getElementById('taskDescription').value.trim();
  const due = document.getElementById('taskDue').value;
  const priority = document.getElementById('taskPriority').value;

  if (!summary) {
    showToast('Task summary is required', 'warning');
    return;
  }

  const taskData = {
    summary, // Use 'summary' not 'title'
    description: description || undefined,
    due: due ? new Date(due).toISOString() : undefined,
    priority: priority ? parseInt(priority) : undefined,
    calendar_url: currentCalendar
  };

  try {
    const res = await fetch('/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(taskData)
    });

    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}`);
    }

    const result = await res.json();
    document.getElementById('taskForm').reset();
    showToast(result.status === 'created' ? 'Task created successfully' : 'Task created', 'success', 2000);
    fetchTasks();
    
  } catch (error) {
    console.error('Failed to create task:', error);
    showToast('Failed to create task: ' + error.message, 'error');
  }
});

document.getElementById('syncBtn').addEventListener('click', async () => {
  if (!currentCalendar) {
    showToast('Please select a calendar first', 'warning');
    return;
  }
  
  try {
    const url = `/sync?calendar_url=${encodeURIComponent(currentCalendar)}`;
    const res = await fetch(url, { method: 'POST' });
    
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}`);
    }
    
    const result = await res.json();
    showToast(`Sync completed: ${result.tasks_synced} tasks synced`, 'success');
    fetchTasks();
    
  } catch (error) {
    console.error('Sync failed:', error);
    showToast('Sync failed: ' + error.message, 'error');
  }
});

document.getElementById('pushPendingBtn').addEventListener('click', async () => {
  if (!currentCalendar) {
    showToast("No calendar selected", 'warning');
    return;
  }
  
  try {
    const res = await fetch(`/push_pending?calendar_url=${encodeURIComponent(currentCalendar)}`, { 
      method: 'POST' 
    });
    
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}`);
    }
    
    const result = await res.json();
    const message = result.pushed > 0 
      ? `Pushed ${result.pushed} pending tasks successfully`
      : 'No pending tasks to push';
    showToast(message, 'success');
    
    if (result.errors && result.errors.length > 0) {
      showToast(`${result.errors.length} tasks failed to push`, 'warning');
    }
    
    fetchTasks();
    
  } catch (error) {
    console.error('Push failed:', error);
    showToast('Push failed: ' + error.message, 'error');
  }
});

document.getElementById('toggleCompletedBtn').addEventListener('click', () => {
  const completedDiv = document.getElementById('completedTasks');
  const visible = completedDiv.style.display !== 'none';
  completedDiv.style.display = visible ? 'none' : 'block';
  document.getElementById('toggleCompletedBtn').textContent = visible
    ? "Show Completed Tasks ▾"
    : "Hide Completed Tasks ▴";
});


function initializeViewControls() {
  document.getElementById('listViewBtn').addEventListener('click', () => {
    currentView = 'list';
    document.getElementById('listViewBtn').classList.add('active');
    document.getElementById('boardViewBtn').classList.remove('active');
    document.getElementById('listViewBtn').setAttribute('aria-pressed', 'true');
    document.getElementById('boardViewBtn').setAttribute('aria-pressed', 'false');
    
    // Re-render with current tasks
    const container = document.getElementById('taskList');
    container.className = 'task-list'; // Reset to list class
    renderTasks(allActiveTasks, container);
  });

  document.getElementById('boardViewBtn').addEventListener('click', () => {
    currentView = 'board';
    document.getElementById('boardViewBtn').classList.add('active');
    document.getElementById('listViewBtn').classList.remove('active');
    document.getElementById('boardViewBtn').setAttribute('aria-pressed', 'true');
    document.getElementById('listViewBtn').setAttribute('aria-pressed', 'false');
    
    // Re-render with current tasks
    const container = document.getElementById('taskList');
    renderTasks(allActiveTasks, container);
  });

  // Priority filter implementation
  document.getElementById('priorityFilter').addEventListener('change', (e) => {
    currentPriorityFilter = e.target.value;
    console.log('Filter by priority:', currentPriorityFilter);
    
    // Re-render the task list with filter applied
    const container = document.getElementById('taskList');
    renderTasks(allActiveTasks, container);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  // Hide the initial loading overlay
  document.getElementById('loading-indicator').style.display = 'none';
  
  // Initialize view controls
  initializeViewControls();
  
  fetchCalendars();

  flatpickr("#taskDue", {
    enableTime: true,
    dateFormat: "Y-m-d\\TH:i",
    altInput: true,
    altFormat: "F j, Y (h:i K)"
  });
});
