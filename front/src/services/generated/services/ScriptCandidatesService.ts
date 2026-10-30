/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_list_ScriptCandidateRead__ } from '../models/ApiResponse_list_ScriptCandidateRead__';
import type { ApiResponse_ScriptCandidateAvailability_ } from '../models/ApiResponse_ScriptCandidateAvailability_';
import type { ApiResponse_ScriptCandidateRead_ } from '../models/ApiResponse_ScriptCandidateRead_';
import type { ScriptCandidateCreate } from '../models/ScriptCandidateCreate';
import type { ScriptCandidateDecision } from '../models/ScriptCandidateDecision';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ScriptCandidatesService {
    /**
     * Candidate Availability
     * Inspect saved source and exact model price; this GET never submits a model request.
     * @returns ApiResponse_ScriptCandidateAvailability_ Successful Response
     * @throws ApiError
     */
    public static candidateAvailabilityApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesAvailabilityGet({
        projectId,
        chapterId,
        operation = 'optimize',
        sourceField = 'raw_text',
        shotId,
        modelId,
    }: {
        projectId: string,
        chapterId: string,
        operation?: 'optimize' | 'simplify' | 'divide' | 'extract_shot',
        sourceField?: string,
        shotId?: (string | null),
        modelId?: (string | null),
    }): CancelablePromise<ApiResponse_ScriptCandidateAvailability_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/script-preparation/projects/{project_id}/chapters/{chapter_id}/candidates/availability',
            path: {
                'project_id': projectId,
                'chapter_id': chapterId,
            },
            query: {
                'operation': operation,
                'source_field': sourceField,
                'shot_id': shotId,
                'model_id': modelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Script Candidates
     * Recover the actual proposal history for this chapter after refresh.
     * @returns ApiResponse_list_ScriptCandidateRead__ Successful Response
     * @throws ApiError
     */
    public static listScriptCandidatesApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesGet({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_list_ScriptCandidateRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/script-preparation/projects/{project_id}/chapters/{chapter_id}/candidates',
            path: {
                'project_id': projectId,
                'chapter_id': chapterId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Script Candidate
     * Authorize one bounded, original-project call and recover exact repeated intent.
     * @returns ApiResponse_ScriptCandidateRead_ Successful Response
     * @throws ApiError
     */
    public static createScriptCandidateApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesPost({
        projectId,
        chapterId,
        requestBody,
    }: {
        projectId: string,
        chapterId: string,
        requestBody: ScriptCandidateCreate,
    }): CancelablePromise<ApiResponse_ScriptCandidateRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/script-preparation/projects/{project_id}/chapters/{chapter_id}/candidates',
            path: {
                'project_id': projectId,
                'chapter_id': chapterId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Script Candidate
     * Read a validated candidate without importing its text into human edits.
     * @returns ApiResponse_ScriptCandidateRead_ Successful Response
     * @throws ApiError
     */
    public static getScriptCandidateApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesCandidateIdGet({
        projectId,
        chapterId,
        candidateId,
    }: {
        projectId: string,
        chapterId: string,
        candidateId: string,
    }): CancelablePromise<ApiResponse_ScriptCandidateRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/script-preparation/projects/{project_id}/chapters/{chapter_id}/candidates/{candidate_id}',
            path: {
                'project_id': projectId,
                'chapter_id': chapterId,
                'candidate_id': candidateId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Adopt Script Candidate
     * Adopt the exact reviewed result only if its original saved source is still current.
     * @returns ApiResponse_ScriptCandidateRead_ Successful Response
     * @throws ApiError
     */
    public static adoptScriptCandidateApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesCandidateIdAdoptPost({
        projectId,
        chapterId,
        candidateId,
        requestBody,
    }: {
        projectId: string,
        chapterId: string,
        candidateId: string,
        requestBody: ScriptCandidateDecision,
    }): CancelablePromise<ApiResponse_ScriptCandidateRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/script-preparation/projects/{project_id}/chapters/{chapter_id}/candidates/{candidate_id}/adopt',
            path: {
                'project_id': projectId,
                'chapter_id': chapterId,
                'candidate_id': candidateId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Dismiss Script Candidate
     * Ignore a proposal and cancel unsent work; sent model fees remain unknown.
     * @returns ApiResponse_ScriptCandidateRead_ Successful Response
     * @throws ApiError
     */
    public static dismissScriptCandidateApiV1ScriptPreparationProjectsProjectIdChaptersChapterIdCandidatesCandidateIdDismissPost({
        projectId,
        chapterId,
        candidateId,
    }: {
        projectId: string,
        chapterId: string,
        candidateId: string,
    }): CancelablePromise<ApiResponse_ScriptCandidateRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/script-preparation/projects/{project_id}/chapters/{chapter_id}/candidates/{candidate_id}/dismiss',
            path: {
                'project_id': projectId,
                'chapter_id': chapterId,
                'candidate_id': candidateId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
