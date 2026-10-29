/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRunCreate } from '../models/AgentRunCreate';
import type { AgentRunRetry } from '../models/AgentRunRetry';
import type { ApiResponse_AgentExecutionAvailability_ } from '../models/ApiResponse_AgentExecutionAvailability_';
import type { ApiResponse_AgentRunRead_ } from '../models/ApiResponse_AgentRunRead_';
import type { ApiResponse_PaginatedData_AgentRunRead__ } from '../models/ApiResponse_PaginatedData_AgentRunRead__';
import type { ModelConnectionTestCreate } from '../models/ModelConnectionTestCreate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentExecutionService {
    /**
     * Get Agent Execution Availability
     * Read configuration readiness without issuing any model request or returning credentials.
     * @returns ApiResponse_AgentExecutionAvailability_ Successful Response
     * @throws ApiError
     */
    public static getAgentExecutionAvailability({
        modelId,
    }: {
        modelId?: (string | null),
    }): CancelablePromise<ApiResponse_AgentExecutionAvailability_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agent-execution/availability',
            query: {
                'model_id': modelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Agent Run
     * Admit a version-pinned workflow; the scanner sees it only after a successful commit.
     * @returns ApiResponse_AgentRunRead_ Successful Response
     * @throws ApiError
     */
    public static createAgentRun({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: AgentRunCreate,
    }): CancelablePromise<ApiResponse_AgentRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/agent-execution/agents/{agent_id}/runs',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Agent Runs
     * Browse concrete attempts and node logs after refresh or process restart.
     * @returns ApiResponse_PaginatedData_AgentRunRead__ Successful Response
     * @throws ApiError
     */
    public static listAgentRuns({
        agentId,
        page = 1,
        pageSize = 20,
    }: {
        agentId: string,
        page?: number,
        pageSize?: number,
    }): CancelablePromise<ApiResponse_PaginatedData_AgentRunRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agent-execution/agents/{agent_id}/runs',
            path: {
                'agent_id': agentId,
            },
            query: {
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Agent Run
     * Inspect the real current attempt rather than a generated demo result.
     * @returns ApiResponse_AgentRunRead_ Successful Response
     * @throws ApiError
     */
    public static getAgentRun({
        runId,
    }: {
        runId: string,
    }): CancelablePromise<ApiResponse_AgentRunRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agent-execution/runs/{run_id}',
            path: {
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel Agent Run
     * Persist cancellation before the provider watcher can accept a late result.
     * @returns ApiResponse_AgentRunRead_ Successful Response
     * @throws ApiError
     */
    public static cancelAgentRun({
        runId,
    }: {
        runId: string,
    }): CancelablePromise<ApiResponse_AgentRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/agent-execution/runs/{run_id}/cancel',
            path: {
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Retry Agent Run
     * Explicitly authorize a new full attempt with the prior request's immutable inputs.
     * @returns ApiResponse_AgentRunRead_ Successful Response
     * @throws ApiError
     */
    public static retryAgentRun({
        runId,
        requestBody,
    }: {
        runId: string,
        requestBody: AgentRunRetry,
    }): CancelablePromise<ApiResponse_AgentRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/agent-execution/runs/{run_id}/retry',
            path: {
                'run_id': runId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Model Connection Test
     * Authorize one low-volume real text request; this does not qualify image/video generation.
     * @returns ApiResponse_AgentRunRead_ Successful Response
     * @throws ApiError
     */
    public static createModelConnectionTest({
        modelId,
        requestBody,
    }: {
        modelId: string,
        requestBody: ModelConnectionTestCreate,
    }): CancelablePromise<ApiResponse_AgentRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/agent-execution/models/{model_id}/connection-tests',
            path: {
                'model_id': modelId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Model Connection Tests
     * Retain real connection outcomes and sanitized failures instead of a static success badge.
     * @returns ApiResponse_PaginatedData_AgentRunRead__ Successful Response
     * @throws ApiError
     */
    public static listModelConnectionTests({
        modelId,
        page = 1,
        pageSize = 20,
    }: {
        modelId: string,
        page?: number,
        pageSize?: number,
    }): CancelablePromise<ApiResponse_PaginatedData_AgentRunRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agent-execution/models/{model_id}/connection-tests',
            path: {
                'model_id': modelId,
            },
            query: {
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
