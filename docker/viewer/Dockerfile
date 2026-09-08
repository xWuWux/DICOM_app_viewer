FROM nginx:1.27-alpine

# .envsh files here are sourced by the base image's own entrypoint, before its
# 20-envsubst-on-templates.sh substitutes *.template files under
# /etc/nginx/templates/ into /etc/nginx/conf.d/ -- both are stock nginx:alpine
# conventions, not custom to this project.
COPY 15-compute-orthanc-auth.envsh /docker-entrypoint.d/
RUN chmod +x /docker-entrypoint.d/15-compute-orthanc-auth.envsh
COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY watermark.html /usr/share/nginx/html/watermark.html

EXPOSE 8080
