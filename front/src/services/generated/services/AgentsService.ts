/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentConfigCreate } from '../models/AgentConfigCreate';
import type { AgentConfigExport_Input } from '../models/AgentConfigExport_Input';
import type { AgentConfigUpdate } from '../models/AgentConfigUpdate';
import type { ApiResponse_AgentConfigExport_ } from '../models/ApiResponse_AgentConfigExport_';
import type { ApiResponse_AgentConfigRead_ } from '../models/ApiResponse_AgentConfigRead_';
import type { ApiResponse_list_AgentConfigRevisionRead__ } from '../models/ApiResponse_list_AgentConfigRevisionRead__';
import type { ApiResponse_NoneType_ } from '../models/ApiResponse_NoneType_';
import type { ApiResponse_PaginatedData_AgentConfigRead__ } from '../models/ApiResponse_PaginatedData_AgentConfigRead__';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentsService {
    /**
     * List Agent Configs
     * Browse persisted configs; the empty state is a successful empty collection.
     * @returns ApiResponse_PaginatedData_AgentConfigRead__ Successful Response
     * @throws ApiError
     */
    public static listAgentConfigs({
        q,
        page = 1,
        pageSize = 20,
    }: {
        q?: (string | null),
        page?: number,
        pageSize?: number,
    }): CancelablePromise<ApiResponse_PaginatedData_AgentConfigRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agents',
            query: {
                'q': q,
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Agent Config
     * Save complete settings and first history without executing a model.
     * @returns ApiResponse_AgentConfigRead_ Successful Response
     * @throws ApiError
     */
    public static createAgentConfig({
        requestBody,
    }: {
        requestBody: AgentConfigCreate,
    }): CancelablePromise<ApiResponse_AgentConfigRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/agents',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Import Agent Config
     * Import a bounded portable config under a new database identity.
     * @returns ApiResponse_AgentConfigRead_ Successful Response
     * @throws ApiError
     */
    public static importAgentConfig({
        requestBody,
    }: {
        requestBody: AgentConfigExport_Input,
    }): CancelablePromise<ApiResponse_AgentConfigRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/agents/import',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Agent Config
     * Load complete saved metadata and workflow draft.
     * @returns ApiResponse_AgentConfigRead_ Successful Response
     * @throws ApiError
     */
    public static getAgentConfig({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<ApiResponse_AgentConfigRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agents/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Agent Config
     * Persist edits or favorite/default changes with an optional version guard.
     * @returns ApiResponse_AgentConfigRead_ Successful Response
     * @throws ApiError
     */
    public static updateAgentConfig({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: AgentConfigUpdate,
    }): CancelablePromise<ApiResponse_AgentConfigRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/agents/{agent_id}',
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
     * Delete Agent Config
     * Delete a config and its own revision history, leaving every other config intact.
     * @returns ApiResponse_NoneType_ Successful Response
     * @throws ApiError
     */
    public static deleteAgentConfig({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<ApiResponse_NoneType_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/agents/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Copy Agent Config
     * Create a real, independently editable copy.
     * @returns ApiResponse_AgentConfigRead_ Successful Response
     * @throws ApiError
     */
    public static copyAgentConfig({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<ApiResponse_AgentConfigRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/agents/{agent_id}/copy',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Export Agent Config
     * Export portable settings; file download is handled by the caller after success.
     * @returns ApiResponse_AgentConfigExport_ Successful Response
     * @throws ApiError
     */
    public static exportAgentConfig({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<ApiResponse_AgentConfigExport_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agents/{agent_id}/export',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Agent Config Revisions
     * Inspect actual saved versions instead of a fabricated history list.
     * @returns ApiResponse_list_AgentConfigRevisionRead__ Successful Response
     * @throws ApiError
     */
    public static listAgentConfigRevisions({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<ApiResponse_list_AgentConfigRevisionRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agents/{agent_id}/revisions',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
