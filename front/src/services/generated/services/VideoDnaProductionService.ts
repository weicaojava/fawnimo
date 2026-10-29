/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_dict_ } from '../models/ApiResponse_dict_';
import type { ApiResponse_DnaProductionRead_ } from '../models/ApiResponse_DnaProductionRead_';
import type { DnaProductionWrite } from '../models/DnaProductionWrite';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class VideoDnaProductionService {
    /**
     * Preview Dna Production
     * Show exact counts and prior import identity before a user writes any records.
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static previewDnaProductionApiV1VideoDnaProjectsProjectIdDraftsDraftIdProductionPreviewGet({
        projectId,
        draftId,
    }: {
        projectId: string,
        draftId: string,
    }): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/video-dna/projects/{project_id}/drafts/{draft_id}/production-preview',
            path: {
                'project_id': projectId,
                'draft_id': draftId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Import Dna Production
     * Import one revision atomically, while leaving all production states pending.
     * @returns ApiResponse_DnaProductionRead_ Successful Response
     * @throws ApiError
     */
    public static importDnaProductionApiV1VideoDnaProjectsProjectIdDraftsDraftIdProductionImportsPost({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: DnaProductionWrite,
    }): CancelablePromise<ApiResponse_DnaProductionRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/video-dna/projects/{project_id}/drafts/{draft_id}/production-imports',
            path: {
                'project_id': projectId,
                'draft_id': draftId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
