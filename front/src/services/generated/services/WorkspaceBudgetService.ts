/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_WorkspaceBudgetPage_ } from '../models/ApiResponse_WorkspaceBudgetPage_';
import type { ApiResponse_WorkspaceBudgetRead_ } from '../models/ApiResponse_WorkspaceBudgetRead_';
import type { WorkspaceBudgetWrite } from '../models/WorkspaceBudgetWrite';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class WorkspaceBudgetService {
    /**
     * Get Workspace Budget
     * Return a coherent shared ceiling and actual held amounts without remote calls.
     * @returns ApiResponse_WorkspaceBudgetRead_ Successful Response
     * @throws ApiError
     */
    public static getWorkspaceBudget(): CancelablePromise<ApiResponse_WorkspaceBudgetRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-runtime/workspace-budget',
        });
    }
    /**
     * Put Workspace Budget
     * Trust only gateway-injected role and actor after the worker's private token fence.
     * @returns ApiResponse_WorkspaceBudgetRead_ Successful Response
     * @throws ApiError
     */
    public static saveWorkspaceBudget({
        requestBody,
    }: {
        requestBody: WorkspaceBudgetWrite,
    }): CancelablePromise<ApiResponse_WorkspaceBudgetRead_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/v1/video-runtime/workspace-budget',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Workspace Budget Entries
     * List bounded local accounting details; never expose model inputs or connection secrets.
     * @returns ApiResponse_WorkspaceBudgetPage_ Successful Response
     * @throws ApiError
     */
    public static getWorkspaceBudgetEntries({
        after,
        limit = 20,
    }: {
        after?: (string | null),
        limit?: number,
    }): CancelablePromise<ApiResponse_WorkspaceBudgetPage_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-runtime/workspace-budget/entries',
            query: {
                'after': after,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
