/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_AssetRegistrationRead_ } from '../models/ApiResponse_AssetRegistrationRead_';
import type { ApiResponse_CompiledPlanRead_ } from '../models/ApiResponse_CompiledPlanRead_';
import type { ApiResponse_list_PlannedJobRead__ } from '../models/ApiResponse_list_PlannedJobRead__';
import type { ApiResponse_list_PlanRead__ } from '../models/ApiResponse_list_PlanRead__';
import type { ApiResponse_PlanCatalog_ } from '../models/ApiResponse_PlanCatalog_';
import type { ApiResponse_PlannedSubmissionRead_ } from '../models/ApiResponse_PlannedSubmissionRead_';
import type { ApiResponse_PlanPreflight_ } from '../models/ApiResponse_PlanPreflight_';
import type { ApiResponse_PlanRead_ } from '../models/ApiResponse_PlanRead_';
import type { AssetRegistration } from '../models/AssetRegistration';
import type { PlanCompile } from '../models/PlanCompile';
import type { PlanCreate } from '../models/PlanCreate';
import type { PlannedSubmission } from '../models/PlannedSubmission';
import type { PlanPatch } from '../models/PlanPatch';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ShotPlansService {
    /**
     * Get Shot Plan Catalog
     * List complete bounded actual chapter choices and public trusted policy metadata.
     * @returns ApiResponse_PlanCatalog_ Successful Response
     * @throws ApiError
     */
    public static getShotPlanCatalogApiV1ShotPlansProjectsProjectIdCatalogGet({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_PlanCatalog_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/shot-plans/projects/{project_id}/catalog',
            path: {
                'project_id': projectId,
            },
            query: {
                'chapter_id': chapterId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Register Shot Plan Asset
     * Create an initial asset version for an existing project identity, never a duplicate character.
     * @returns ApiResponse_AssetRegistrationRead_ Successful Response
     * @throws ApiError
     */
    public static registerShotPlanAssetApiV1ShotPlansProjectsProjectIdAssetRegistrationsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: AssetRegistration,
    }): CancelablePromise<ApiResponse_AssetRegistrationRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/shot-plans/projects/{project_id}/asset-registrations',
            path: {
                'project_id': projectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Shot Plan Drafts
     * Read the unique chapter draft and its current preparation issues.
     * @returns ApiResponse_list_PlanRead__ Successful Response
     * @throws ApiError
     */
    public static listShotPlanDraftsApiV1ShotPlansProjectsProjectIdDraftsGet({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_list_PlanRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/shot-plans/projects/{project_id}/drafts',
            path: {
                'project_id': projectId,
            },
            query: {
                'chapter_id': chapterId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Shot Plan Draft
     * Save incomplete preparation without invoking or enabling a model.
     * @returns ApiResponse_PlanRead_ Successful Response
     * @throws ApiError
     */
    public static createShotPlanDraftApiV1ShotPlansProjectsProjectIdDraftsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: PlanCreate,
    }): CancelablePromise<ApiResponse_PlanRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/shot-plans/projects/{project_id}/drafts',
            path: {
                'project_id': projectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Shot Plan Draft
     * Read a single actual owned draft with safe historical plan summaries.
     * @returns ApiResponse_PlanRead_ Successful Response
     * @throws ApiError
     */
    public static getShotPlanDraftApiV1ShotPlansProjectsProjectIdDraftsDraftIdGet({
        projectId,
        draftId,
    }: {
        projectId: string,
        draftId: string,
    }): CancelablePromise<ApiResponse_PlanRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/shot-plans/projects/{project_id}/drafts/{draft_id}',
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
     * Patch Shot Plan Draft
     * Save a CAS revision; historical compiled inputs remain unchanged.
     * @returns ApiResponse_PlanRead_ Successful Response
     * @throws ApiError
     */
    public static patchShotPlanDraftApiV1ShotPlansProjectsProjectIdDraftsDraftIdPatch({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: PlanPatch,
    }): CancelablePromise<ApiResponse_PlanRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/shot-plans/projects/{project_id}/drafts/{draft_id}',
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
    /**
     * Compile Shot Plan
     * Compile a complete chapter to immutable deterministic inputs without charging.
     * @returns ApiResponse_CompiledPlanRead_ Successful Response
     * @throws ApiError
     */
    public static compileShotPlanApiV1ShotPlansProjectsProjectIdDraftsDraftIdCompilePost({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: PlanCompile,
    }): CancelablePromise<ApiResponse_CompiledPlanRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/shot-plans/projects/{project_id}/drafts/{draft_id}/compile',
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
    /**
     * Get Shot Plan
     * Export original preparation evidence without recertifying current validity.
     * @returns ApiResponse_CompiledPlanRead_ Successful Response
     * @throws ApiError
     */
    public static getShotPlanApiV1ShotPlansProjectsProjectIdPlansPlanIdGet({
        projectId,
        planId,
    }: {
        projectId: string,
        planId: string,
    }): CancelablePromise<ApiResponse_CompiledPlanRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/shot-plans/projects/{project_id}/plans/{plan_id}',
            path: {
                'project_id': projectId,
                'plan_id': planId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Preflight Shot Plan
     * Separate current content usability from runtime, policy and budget availability.
     * @returns ApiResponse_PlanPreflight_ Successful Response
     * @throws ApiError
     */
    public static preflightShotPlanApiV1ShotPlansProjectsProjectIdPlansPlanIdPreflightGet({
        projectId,
        planId,
    }: {
        projectId: string,
        planId: string,
    }): CancelablePromise<ApiResponse_PlanPreflight_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/shot-plans/projects/{project_id}/plans/{plan_id}/preflight',
            path: {
                'project_id': projectId,
                'plan_id': planId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Submit Planned Shot
     * Replay original jobs before new eligibility checks; never accept client execution inputs.
     * @returns ApiResponse_PlannedSubmissionRead_ Successful Response
     * @throws ApiError
     */
    public static submitPlannedShotApiV1ShotPlansProjectsProjectIdPlansPlanIdShotsShotIdSubmissionsPost({
        projectId,
        planId,
        shotId,
        requestBody,
    }: {
        projectId: string,
        planId: string,
        shotId: string,
        requestBody: PlannedSubmission,
    }): CancelablePromise<ApiResponse_PlannedSubmissionRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/shot-plans/projects/{project_id}/plans/{plan_id}/shots/{shot_id}/submissions',
            path: {
                'project_id': projectId,
                'plan_id': planId,
                'shot_id': shotId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Planned Shot Jobs
     * Read lightweight plan/shot/task candidate provenance, without automatic adoption.
     * @returns ApiResponse_list_PlannedJobRead__ Successful Response
     * @throws ApiError
     */
    public static listPlannedShotJobsApiV1ShotPlansProjectsProjectIdPlansPlanIdJobsGet({
        projectId,
        planId,
    }: {
        projectId: string,
        planId: string,
    }): CancelablePromise<ApiResponse_list_PlannedJobRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/shot-plans/projects/{project_id}/plans/{plan_id}/jobs',
            path: {
                'project_id': projectId,
                'plan_id': planId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
