/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_WorkspaceTaskFeed_ } from '../models/ApiResponse_WorkspaceTaskFeed_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class WorkspaceTasksService {
    /**
     * Get Workspace Tasks
     * Read bounded generic job metadata without dispatching a model or loading business outputs.
     * @returns ApiResponse_WorkspaceTaskFeed_ Successful Response
     * @throws ApiError
     */
    public static listWorkspaceTasks({
        limit = 100,
    }: {
        limit?: number,
    }): CancelablePromise<ApiResponse_WorkspaceTaskFeed_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/workspace-tasks/recent',
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
