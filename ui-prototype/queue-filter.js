/**
 * @typedef {{ id: string, title: string, status: string, depends_on?: string[], attempts: number, updated_at: string }} QueueTaskLike
 */

/**
 * Filter the bounded task snapshot already supplied by the API.
 * This helper never changes the source array or implies that truncated rows exist.
 *
 * @param {QueueTaskLike[]} tasks
 * @param {string} query
 * @param {string} status
 * @returns {QueueTaskLike[]}
 */
export function filterQueueTasks(tasks, query = "", status = "all") {
  const needle = String(query ?? "").trim().toLocaleLowerCase();
  const selectedStatus = String(status ?? "all");

  return tasks.filter((task) => {
    if (selectedStatus !== "all" && task.status !== selectedStatus) return false;
    if (!needle) return true;
    return [task.title, task.id, task.status]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase()
      .includes(needle);
  });
}
