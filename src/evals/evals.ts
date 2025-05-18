//evals.ts

import { EvalConfig } from 'mcp-evals';
import { openai } from "@ai-sdk/openai";
import { grade, EvalFunction } from "mcp-evals";

const get_issue_worklogsEval: EvalFunction = {
  name: "get_issue_worklogs Tool Evaluation",
  description: "Evaluates the get_issue_worklogs tool functionality",
  run: async () => {
    const result = await grade(openai("gpt-4"), "Please retrieve all worklogs for the project with ID 'test_project_id' and the issue with ID 'test_issue_id' using the get_issue_worklogs tool.");
    return JSON.parse(result);
  }
};

const get_total_worklogsEval: EvalFunction = {
    name: "get_total_worklogs Tool Evaluation",
    description: "Evaluates the get_total_worklogs tool functionality to retrieve total worklogs for a project",
    run: async () => {
        const result = await grade(openai("gpt-4"), "Could you retrieve the total logged time for the project with ID '123e4567-e89b-12d3-a456-426614174000'?");
        return JSON.parse(result);
    }
};

const create_worklogEval: EvalFunction = {
  name: 'create_worklog Tool Evaluation',
  description: 'Evaluates the creation of a new worklog for an issue',
  run: async () => {
    const result = await grade(openai("gpt-4"), "Please create a new worklog for the project with ID 12345 and issue with ID 67890 with a duration of 2 hours and description 'Working on front-end bug'.");
    return JSON.parse(result);
  }
};

const update_worklogEval: EvalFunction = {
    name: "update_worklog Evaluation",
    description: "Evaluates the update_worklog tool's functionality",
    run: async () => {
        const result = await grade(openai("gpt-4"), "Please update the worklog with ID '789' in the project '123' and issue '456' by changing the time spent to '90 minutes' and adding a new comment 'Updated worklog due to additional work'.");
        return JSON.parse(result);
    }
};

const delete_worklogEval: EvalFunction = {
    name: 'delete_worklogEval',
    description: 'Evaluates the functionality of the delete_worklog tool',
    run: async () => {
        const result = await grade(openai("gpt-4"), "Please delete the worklog with ID 'worklog_123' from the issue 'issue_456' in the project 'project_789'. Verify that the worklog is actually removed.");
        return JSON.parse(result);
    }
};

const config: EvalConfig = {
    model: openai("gpt-4"),
    evals: [get_issue_worklogsEval, get_total_worklogsEval, create_worklogEval, update_worklogEval, delete_worklogEval]
};
  
export default config;
  
export const evals = [get_issue_worklogsEval, get_total_worklogsEval, create_worklogEval, update_worklogEval, delete_worklogEval];