/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Keep actual owned media metadata usable in generated clients; content remains behind its scoped endpoint.
 */
export type ProductionMediaRead = {
    id: string;
    project_id: string;
    name: string;
    mime: 'image/png' | 'image/jpeg' | 'image/webp' | 'video/mp4' | 'audio/wav';
    sha256: string;
    size: number;
    created_at: string;
    updated_at: string;
};

