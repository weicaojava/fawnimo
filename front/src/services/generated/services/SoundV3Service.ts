/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_list_SoundCandidateRead__ } from '../models/ApiResponse_list_SoundCandidateRead__';
import type { ApiResponse_list_SoundDraftRead__ } from '../models/ApiResponse_list_SoundDraftRead__';
import type { ApiResponse_LocalSpeechHistory_ } from '../models/ApiResponse_LocalSpeechHistory_';
import type { ApiResponse_LocalSpeechRead_ } from '../models/ApiResponse_LocalSpeechRead_';
import type { ApiResponse_SoundCandidateRead_ } from '../models/ApiResponse_SoundCandidateRead_';
import type { ApiResponse_SoundDraftRead_ } from '../models/ApiResponse_SoundDraftRead_';
import type { ApiResponse_SoundExportCatalog_ } from '../models/ApiResponse_SoundExportCatalog_';
import type { ApiResponse_SoundExportRead_ } from '../models/ApiResponse_SoundExportRead_';
import type { ApiResponse_SoundPublicationRead_ } from '../models/ApiResponse_SoundPublicationRead_';
import type { ExportCancel } from '../models/ExportCancel';
import type { ExportRequest } from '../models/ExportRequest';
import type { LocalSpeechRequest } from '../models/LocalSpeechRequest';
import type { SoundCandidateImport } from '../models/SoundCandidateImport';
import type { SoundCreate } from '../models/SoundCreate';
import type { SoundFreeze } from '../models/SoundFreeze';
import type { SoundPatch } from '../models/SoundPatch';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SoundV3Service {
    /**
     * List Sound Drafts
     * List actual variants in a chapter; this read never creates data.
     * @returns ApiResponse_list_SoundDraftRead__ Successful Response
     * @throws ApiError
     */
    public static listSoundDrafts({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_list_SoundDraftRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/sound-v3/projects/{project_id}/drafts',
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
     * Create Sound Draft
     * Create an independent sound form with an explicitly copied whole picture document.
     * @returns ApiResponse_SoundDraftRead_ Successful Response
     * @throws ApiError
     */
    public static createSoundDraft({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: SoundCreate,
    }): CancelablePromise<ApiResponse_SoundDraftRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/sound-v3/projects/{project_id}/drafts',
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
     * Patch Sound Draft
     * Save the complete picture and sound form using the expected saved revision.
     * @returns ApiResponse_SoundDraftRead_ Successful Response
     * @throws ApiError
     */
    public static patchSoundDraft({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: SoundPatch,
    }): CancelablePromise<ApiResponse_SoundDraftRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/sound-v3/projects/{project_id}/drafts/{draft_id}',
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
     * Freeze Sound Draft
     * Lock verified original sources and explicit overlap into a new sound publication.
     * @returns ApiResponse_SoundPublicationRead_ Successful Response
     * @throws ApiError
     */
    public static freezeSoundDraft({
        projectId,
        draftId,
        requestBody,
    }: {
        projectId: string,
        draftId: string,
        requestBody: SoundFreeze,
    }): CancelablePromise<ApiResponse_SoundPublicationRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/sound-v3/projects/{project_id}/drafts/{draft_id}/freeze',
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
     * Get Sound Publication
     * Inspect immutable sound history without replacing v1 or current adoptions.
     * @returns ApiResponse_SoundPublicationRead_ Successful Response
     * @throws ApiError
     */
    public static getSoundPublication({
        projectId,
        publicationId,
    }: {
        projectId: string,
        publicationId: string,
    }): CancelablePromise<ApiResponse_SoundPublicationRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/sound-v3/projects/{project_id}/publications/{publication_id}',
            path: {
                'project_id': projectId,
                'publication_id': publicationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Sound Exports
     * Read only the independent sound render queue and actual local tool availability.
     * @returns ApiResponse_SoundExportCatalog_ Successful Response
     * @throws ApiError
     */
    public static listSoundExports({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_SoundExportCatalog_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/sound-v3/projects/{project_id}/exports',
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
     * Create Sound Export
     * Queue a real picture and independently placed audio render with a durable replay key.
     * @returns ApiResponse_SoundExportRead_ Successful Response
     * @throws ApiError
     */
    public static createSoundExport({
        projectId,
        publicationId,
        requestBody,
    }: {
        projectId: string,
        publicationId: string,
        requestBody: ExportRequest,
    }): CancelablePromise<ApiResponse_SoundExportRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/sound-v3/projects/{project_id}/publications/{publication_id}/exports',
            path: {
                'project_id': projectId,
                'publication_id': publicationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Sound Export
     * Read a durable sound outcome and verify every downloadable output.
     * @returns ApiResponse_SoundExportRead_ Successful Response
     * @throws ApiError
     */
    public static getSoundExport({
        projectId,
        exportId,
    }: {
        projectId: string,
        exportId: string,
    }): CancelablePromise<ApiResponse_SoundExportRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/sound-v3/projects/{project_id}/exports/{export_id}',
            path: {
                'project_id': projectId,
                'export_id': exportId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel Sound Export
     * Cancel only this owned local sound job; no model budget is touched.
     * @returns ApiResponse_SoundExportRead_ Successful Response
     * @throws ApiError
     */
    public static cancelSoundExport({
        projectId,
        exportId,
        requestBody,
    }: {
        projectId: string,
        exportId: string,
        requestBody: ExportCancel,
    }): CancelablePromise<ApiResponse_SoundExportRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/sound-v3/projects/{project_id}/exports/{export_id}/cancel',
            path: {
                'project_id': projectId,
                'export_id': exportId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Sound Export Content
     * Stream fixed-name outputs only after source ownership and full byte hashes are checked.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static readSoundExportContent({
        projectId,
        exportId,
        kind,
    }: {
        projectId: string,
        exportId: string,
        kind: 'video' | 'mixdown' | 'subtitles_srt' | 'subtitles_vtt' | 'package',
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/sound-v3/projects/{project_id}/exports/{export_id}/content/{kind}',
            path: {
                'project_id': projectId,
                'export_id': exportId,
                'kind': kind,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Sound Candidates
     * List actual candidates; this read never creates or adopts audio.
     * @returns ApiResponse_list_SoundCandidateRead__ Successful Response
     * @throws ApiError
     */
    public static listSoundCandidates({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_list_SoundCandidateRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/sound-v3/projects/{project_id}/candidates',
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
     * Import Sound Candidate
     * Register a fully decoded actual WAV; explicit track adoption remains a later action.
     * @returns ApiResponse_SoundCandidateRead_ Successful Response
     * @throws ApiError
     */
    public static importSoundCandidate({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: SoundCandidateImport,
    }): CancelablePromise<ApiResponse_SoundCandidateRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/sound-v3/projects/{project_id}/candidates/import',
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
     * List Local Speech
     * Inspect actual local voices and historical jobs without synthesis or paid traffic.
     * @returns ApiResponse_LocalSpeechHistory_ Successful Response
     * @throws ApiError
     */
    public static listLocalSpeech({
        projectId,
        chapterId,
    }: {
        projectId: string,
        chapterId: string,
    }): CancelablePromise<ApiResponse_LocalSpeechHistory_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/sound-v3/projects/{project_id}/speech',
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
     * Create Local Speech
     * Queue exact original dialogue at zero external fee; result adoption is a separate user action.
     * @returns ApiResponse_LocalSpeechRead_ Successful Response
     * @throws ApiError
     */
    public static createLocalSpeech({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: LocalSpeechRequest,
    }): CancelablePromise<ApiResponse_LocalSpeechRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/sound-v3/projects/{project_id}/speech',
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
     * Get Local Speech
     * Recover the original durable job even if the current voice or engine is unavailable.
     * @returns ApiResponse_LocalSpeechRead_ Successful Response
     * @throws ApiError
     */
    public static getLocalSpeech({
        projectId,
        jobId,
    }: {
        projectId: string,
        jobId: string,
    }): CancelablePromise<ApiResponse_LocalSpeechRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/sound-v3/projects/{project_id}/speech/{job_id}',
            path: {
                'project_id': projectId,
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel Local Speech
     * Request cancellation from the owner of the local synthesis process.
     * @returns ApiResponse_LocalSpeechRead_ Successful Response
     * @throws ApiError
     */
    public static cancelLocalSpeech({
        projectId,
        jobId,
        requestBody,
    }: {
        projectId: string,
        jobId: string,
        requestBody: ExportCancel,
    }): CancelablePromise<ApiResponse_LocalSpeechRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/sound-v3/projects/{project_id}/speech/{job_id}/cancel',
            path: {
                'project_id': projectId,
                'job_id': jobId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
